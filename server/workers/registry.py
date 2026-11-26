"""队员注册表：内置队员在 workers/，自建和导入的在 data/workers/。

每个队员的偏好（头像、能用哪些全局技能和连接器）存在 data/workers/_state.json，内置队员也能改。
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
from pathlib import Path

from .. import db
from . import package, validate


def _builtin_dir() -> Path:
    """源码运行在 <仓库>/workers；PyInstaller 冻结后在解包目录 sys._MEIPASS/workers。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "workers"
    return db.ROOT / "workers"


BUILTIN_DIR = _builtin_dir()
GENERAL_ID = "general-assistant"
_lock = threading.RLock()


def user_dir() -> Path:
    d = db.DATA_DIR / "workers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path() -> Path:
    return user_dir() / "_state.json"


def _state() -> dict:
    p = _state_path()
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def _save_state(s: dict) -> None:
    _state_path().write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")


_cards: dict[str, package.WorkerCard] = {}


def _teams() -> dict:
    f = BUILTIN_DIR / "_teams.json"
    try:
        return json.loads(f.read_text("utf-8")) if f.exists() else {}
    except json.JSONDecodeError:
        return {}


def reload() -> None:
    with _lock:
        _cards.clear()
        for d in sorted(BUILTIN_DIR.iterdir()) if BUILTIN_DIR.exists() else []:
            if validate.manifest_path(d):
                c = package.load(d, "builtin")
                _cards[c.id] = c
        st = _state()
        for d in sorted(user_dir().iterdir()):
            if not validate.manifest_path(d):
                continue
            try:
                c = package.load(d, st.get(d.name, {}).get("source", "custom"))
            except Exception:
                continue
            if c.id in _cards:
                continue
            c.enabled = st.get(c.id, {}).get("enabled", True)
            _cards[c.id] = c
        from .. import external
        for c in external.to_worker_cards():
            if c.id not in _cards:
                _cards[c.id] = c
        teams = _teams()
        for c in _cards.values():
            c.team = st.get(c.id, {}).get("team") or c.team or teams.get(c.id, "")


def all_cards() -> list[package.WorkerCard]:
    if not _cards:
        reload()
    return list(_cards.values())


def active() -> list[package.WorkerCard]:
    return [c for c in all_cards() if c.enabled and c.kind in ("cc", "acp")]


def get(wid: str | None) -> package.WorkerCard | None:
    if not _cards:
        reload()
    return _cards.get(wid or "")


def general() -> package.WorkerCard:
    return get(GENERAL_ID) or active()[0]


def install(pkg_root: Path, source: str, enabled: bool) -> tuple[validate.Report, package.WorkerCard | None]:
    """校验后把包根复制进 data/workers/<agent-id>/。"""
    rep = validate.validate_dir(pkg_root)
    if not rep.ok:
        return rep, None
    card = package.load(pkg_root, source)
    with _lock:
        if get(card.id):
            rep.errors.append(f"已有同名队员：{card.id}")
            return rep, None
        dest = user_dir() / card.id
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(pkg_root, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        st = _state()
        st[card.id] = {"enabled": enabled, "source": source}
        _save_state(st)
        reload()
    return rep, get(card.id)


PREF_KEYS = ("avatar", "skills", "connectors", "team")


def prefs(wid: str) -> dict:
    """skills / connectors 为 None 表示全给，列表表示只给勾选的。"""
    st = _state().get(wid, {})
    return {"avatar": st.get("avatar") or "", "skills": st.get("skills"), "connectors": st.get("connectors")}


def set_prefs(wid: str, **kw) -> bool:
    c = get(wid)
    if not c:
        return False
    st = _state()
    row = st.setdefault(wid, {"source": c.source})
    for k, v in kw.items():
        if k in PREF_KEYS:
            row[k] = v
    _save_state(st)
    return True


def set_enabled(wid: str, enabled: bool) -> bool:
    c = get(wid)
    if not c or c.source == "builtin":
        return False
    st = _state()
    st.setdefault(wid, {"source": c.source})["enabled"] = enabled
    _save_state(st)
    reload()
    return True


def remove(wid: str) -> bool:
    c = get(wid)
    if not c or c.source == "builtin":
        return False
    shutil.rmtree(user_dir() / wid, ignore_errors=True)
    st = _state()
    st.pop(wid, None)
    _save_state(st)
    reload()
    return True


def part_name(t: dict | None) -> str:
    """给主人看的「这一块」叫什么：一件事里这个队员只做一块就叫队员名，做好几块就带上这块的名字。不用 A、B 这种代号。"""
    if not t:
        return "Yip"
    if not t.get("parent_id"):
        return "整件事"
    card = get(t.get("worker_id"))
    name = card.name if card else (t.get("worker_id") or "队员")
    from .. import db
    same = [k for k in db.children(t["parent_id"]) if k.get("worker_id") == t.get("worker_id")]
    return f"{name}（{t['title']}）" if len(same) > 1 else name
