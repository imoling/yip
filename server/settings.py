"""设置：data/settings.json（0600，不进 git）。

    {
      "providers": {"deepseek": {"base_url": "...", "api_key": "..."}, ...},   # 用过的模型服务
      "model": "deepseek:deepseek-v4-pro",        # 主模型：拆解、汇总、队员干活
      "fast_model": "deepseek:deepseek-flash",    # 快模型：判断插话；空 = 用主模型
      "yip": {...}                                # Yip 自己的设置，见 YIP_DEFAULTS
    }

旧格式（顶层 base_url / api_key / model / fast_model）读的时候自动迁移；环境变量 LLM_* 只作没有配置文件时的默认值。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import db, providers

MASK = "••••••"
FLAT = ("base_url", "api_key", "model", "fast_model")

YIP_DEFAULTS: dict = {
    "approval": "auto",             # 输入框里的模式：auto 自动 / plan 计划 / ask_writes 手动 / allow_all 全部放行（见 tools/risk.py）
    "overturn_confirm": 0.7,        # 推翻时进度超过它要先确认
    "classify_timeout": 3.0,        # 判断插话最多等快模型几秒
    "low_confidence": 0.6,          # 低于它算没把握，先排着
    "max_parallel": 4,              # 最多同时几个队员
    "max_rounds": 16,               # 每个子任务最多几轮工具调用
    "workspace": "~/Documents/Yip",  # 读写和产出都限定在这里
    # 平时搜索 provider：keenable（默认，免 key）/ ddgs（免 key）/ tavily、bocha（要 key）；深度搜索是 Tavily 连接器
    "search": {"provider": "keenable", "api_key": "", "deep_provider": "", "deep_key": ""},
    "note": "",                     # 给 Yip 的补充说明，拼进拆解和汇总的提示词
    "engine": "classic",            # 队员干活的引擎：classic 自己写的循环；deep 用 LangChain Deep Agents（试验）
    # 人设卡（server/soul.py）：名字、怎么称呼主人、性格、关系、表情、对外自我介绍、白天主动找主人
    "soul": {"name": "", "call_me": "", "tone": "steady", "relation": "assistant", "emoji": "few", "intro": "",
             "proactive": False, "asked": False},
}
APPROVAL = ("auto", "plan", "ask_writes", "allow_all", "ask_all")
ENGINES = ("classic", "deep")


def _path() -> Path:
    return db.DATA_DIR / "settings.json"


def _env_default() -> dict:
    base = os.environ.get("LLM_BASE_URL", "")
    model = os.environ.get("LLM_MODEL", "")
    if not (base and model):
        return {"providers": {}, "model": "", "fast_model": ""}
    pid = providers.guess(base)
    fast = os.environ.get("LLM_FAST_MODEL", "")
    return {"providers": {pid: {"base_url": base, "api_key": os.environ.get("LLM_API_KEY", "")}},
            "model": f"{pid}:{model}", "fast_model": f"{pid}:{fast}" if fast else ""}


def _migrate(raw: dict) -> dict:
    """旧的平铺格式 → providers + "服务:模型"。"""
    if "providers" in raw or not any(k in raw for k in FLAT):
        return raw
    pid = providers.guess(raw.get("base_url", ""))
    out = {k: v for k, v in raw.items() if k not in FLAT}
    out["providers"] = {pid: {"base_url": raw.get("base_url", ""), "api_key": raw.get("api_key", "")}}
    out["model"] = f"{pid}:{raw['model']}" if raw.get("model") else ""
    fast = raw.get("fast_model", "")
    out["fast_model"] = f"{pid}:{fast}" if fast and fast != raw.get("model") else ""
    return out


def load() -> dict:
    p = _path()
    data = _env_default()
    if p.exists():
        try:
            data = _migrate(json.loads(p.read_text("utf-8")))
        except json.JSONDecodeError:
            pass
    data.setdefault("providers", {})
    data.setdefault("model", "")
    data.setdefault("fast_model", "")
    yip = copy.deepcopy(YIP_DEFAULTS)
    for k, v in (data.get("yip") or {}).items():
        if k == "search" and isinstance(v, dict):
            yip["search"].update(v)
        elif k == "soul" and isinstance(v, dict):
            yip["soul"].update(v)
        elif k in yip:
            yip[k] = v
    data["yip"] = yip
    return data


def _write(data: dict) -> None:
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(_path(), 0o600)


def split_ref(ref: str, data: dict | None = None) -> tuple[str, str]:
    """"deepseek:deepseek-v4-pro" → ("deepseek", "deepseek-v4-pro")；模型名里也可能有冒号（qwen3:8b）。"""
    data = data or load()
    if not ref:
        return "", ""
    head, sep, rest = ref.partition(":")
    if sep and (head in data["providers"] or head in providers.BY_ID):
        return head, rest
    first = next(iter(data["providers"]), "")
    return first, ref


def resolve(fast: bool = False, data: dict | None = None) -> dict:
    """给 llm.chat 用的平铺配置：base_url / api_key / model。"""
    data = data or load()
    ref = (data["fast_model"] if fast else "") or data["model"]
    pid, model = split_ref(ref, data)
    prov = data["providers"].get(pid, {})
    base = prov.get("base_url") or (providers.BY_ID[pid].base_url if pid in providers.BY_ID else "")
    return {"provider": pid, "base_url": base, "api_key": prov.get("api_key", ""), "model": model}


def resolve_vision(data: dict | None = None) -> dict | None:
    """看图模型（可选）：主模型看不了图时用它把图读成文字。没配返回 None。"""
    data = data or load()
    ref = data.get("vision_model") or ""
    if not ref:
        return None
    pid, model = split_ref(ref, data)
    prov = data["providers"].get(pid, {})
    base = prov.get("base_url") or (providers.BY_ID[pid].base_url if pid in providers.BY_ID else "")
    if not (base and model):
        return None
    return {"base_url": base, "api_key": prov.get("api_key", ""), "model": model}


def get() -> dict:
    """兼容旧调用：主模型的地址、密钥、模型名，外加快模型名。"""
    main, fast = resolve(False), resolve(True)
    return {"base_url": main["base_url"], "api_key": main["api_key"], "model": main["model"],
            "fast_model": fast["model"] or main["model"]}


def yip() -> dict:
    return load()["yip"]


def workspace() -> Path:
    """SA_WORKSPACE 优先（测试和演示用），否则读设置。"""
    raw = os.environ.get("SA_WORKSPACE") or yip()["workspace"] or "~/Documents/Yip"
    return Path(os.path.expanduser(raw)).resolve()


def configured() -> bool:
    m = resolve(False)
    return bool(m["base_url"] and m["model"])


def public() -> dict:
    data = load()
    provs = {}
    for pid, p in data["providers"].items():
        provs[pid] = {"base_url": p.get("base_url", ""), "api_key": MASK if p.get("api_key") else "",
                      "has_key": bool(p.get("api_key"))}
    y = copy.deepcopy(data["yip"])
    for k in ("api_key", "deep_key"):
        if y["search"].get(k):
            y["search"][k] = MASK
    main = resolve(False, data)
    mail = dict(data.get("mail") or {})
    if mail.get("password"):
        mail["password"] = MASK
    return {"providers": provs, "model": data["model"], "fast_model": data["fast_model"], "mail": mail,
            "vision_model": data.get("vision_model", ""),
            "presets": providers.public(), "yip": y, "configured": configured(),
            "provider": main["provider"], "model_name": main["model"]}


def _clean(v) -> str:
    return str(v if v is not None else "").strip()


def save(patch: dict) -> dict:
    """接受三种写法：providers / model / fast_model / yip 的新格式，或旧的平铺四个字段。"""
    data = load()
    flat = ("base_url" in patch or "api_key" in patch) and "providers" not in patch
    if flat:
        pid = patch.get("provider") or providers.guess(_clean(patch.get("base_url")))
        prov = data["providers"].setdefault(pid, {})
        if "base_url" in patch:
            prov["base_url"] = _clean(patch["base_url"])
        if "api_key" in patch and _clean(patch["api_key"]) != MASK:
            prov["api_key"] = _clean(patch["api_key"])
        if _clean(patch.get("model")):
            data["model"] = f"{pid}:{_clean(patch['model'])}"
        if "fast_model" in patch:
            f = _clean(patch["fast_model"])
            data["fast_model"] = f"{pid}:{f}" if f else ""
    for pid, p in (patch.get("providers") or {}).items():
        if p is None:  # 删掉这个服务
            data["providers"].pop(pid, None)
            continue
        prov = data["providers"].setdefault(pid, {})
        if "base_url" in p:
            prov["base_url"] = _clean(p["base_url"])
        if "api_key" in p and _clean(p["api_key"]) != MASK:
            prov["api_key"] = _clean(p["api_key"])
    if not flat:
        for k in ("model", "fast_model", "vision_model"):
            if k in patch:
                data[k] = _clean(patch[k])
    if isinstance(patch.get("mail"), dict):
        mail = dict(data.get("mail") or {})
        for k, v in patch["mail"].items():
            if k in ("provider", "address", "name", "imap_host", "smtp_host"):
                mail[k] = _clean(v)
            elif k in ("imap_port", "smtp_port"):
                mail[k] = int(v or 0)
            elif k == "password" and _clean(v) and _clean(v) != MASK:
                mail[k] = _clean(v)
        data["mail"] = mail
    if isinstance(patch.get("yip"), dict):
        y = data["yip"]
        for k, v in patch["yip"].items():
            if k == "search" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if not (kk in ("api_key", "deep_key") and vv == MASK)}
                y["search"].update(v)
            elif k == "approval" and v in APPROVAL:
                y[k] = v
            elif k == "engine" and v in ENGINES:
                y[k] = v
            elif k in ("overturn_confirm", "low_confidence"):
                y[k] = min(max(float(v), 0.0), 1.0)
            elif k == "classify_timeout":
                y[k] = min(max(float(v), 0.5), 30.0)
            elif k in ("max_parallel", "max_rounds"):
                y[k] = max(1, min(int(v), 50))
            elif k in ("workspace", "note"):
                y[k] = _clean(v)
            elif k == "soul" and isinstance(v, dict):
                from . import soul
                y["soul"].update(soul.clean_card(v))
    _write(data)
    return public()


def override(body: dict) -> dict:
    """「测试连接」用：界面上还没保存的值，拼成 llm.chat 的平铺 override。"""
    pid = body.get("provider") or ""
    data = load()
    saved = data["providers"].get(pid, {})
    key = _clean(body.get("api_key"))
    if not key or key == MASK:
        key = saved.get("api_key", "")
    base = _clean(body.get("base_url")) or saved.get("base_url") or (
        providers.BY_ID[pid].base_url if pid in providers.BY_ID else "")
    return {"base_url": base, "api_key": key, "model": _clean(body.get("model"))}
