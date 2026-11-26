"""产物：队员写进工作文件夹的文件，和每件事最后汇总出的文档。

同一个路径只记一条：再写一次就更新大小、预览和时间。单个文件只进右栏「产物」，对话里只在整件事交付时出一条。
"""
from __future__ import annotations

from pathlib import Path

from . import db, events

KIND = {".md": "doc", ".txt": "doc", ".docx": "doc", ".pdf": "doc", ".html": "doc",
        ".csv": "sheet", ".tsv": "sheet", ".xlsx": "sheet", ".xls": "sheet", ".json": "sheet",
        ".pptx": "slides", ".ppt": "slides", ".key": "slides"}


def kind_of(p: Path) -> str:
    return KIND.get(p.suffix.lower(), "other")


def preview(p: Path, n: int = 2) -> str:
    if kind_of(p) not in ("doc", "sheet") or p.suffix.lower() in (".docx", ".pdf", ".xlsx", ".xls"):
        return ""
    try:
        lines = [ln.strip(" #>-*\t") for ln in p.read_text("utf-8", errors="replace").splitlines()]
    except OSError:
        return ""
    lines = [ln for ln in lines if ln and not set(ln) <= set("|-: ")]
    return "\n".join(ln[:80] for ln in lines[:n])


def record(ctx, path: Path, *, announce: bool = False) -> int:
    """ctx 只要有 task_id / main_id / worker_id。返回产物 id。"""
    path = Path(path)
    size = path.stat().st_size if path.exists() else 0
    row = db.q1("SELECT * FROM artifacts WHERE path=?", (str(path),))
    pv = preview(path)
    if row:
        db.update("artifacts", row["id"], size=size, preview=pv, created_at=db.now(), task_id=ctx.task_id,
                  worker_id=ctx.worker_id)
        aid = row["id"]
    else:
        aid = db.insert("artifacts", task_id=ctx.task_id, main_id=ctx.main_id, worker_id=ctx.worker_id,
                        path=str(path), name=path.name, kind=kind_of(path), size=size, preview=pv,
                        created_at=db.now())
        if announce:
            t = db.task(ctx.task_id) or {}
            from .workers import registry
            who = registry.part_name(t) if t.get("parent_id") else ""
            events.message(ctx.main_id, "assistant", f"{who}交了 {path.name}", kind="file", artifact_id=aid)
    events.event(ctx.task_id, "artifact", f"写入 {path.name}", artifact_id=aid)
    events.publish("artifact", {"id": aid})
    return aid


def listing(main_id: str | None = None) -> list[dict]:
    if main_id:
        return db.q("SELECT * FROM artifacts WHERE main_id=? ORDER BY created_at DESC", (main_id,))
    return db.q("SELECT * FROM artifacts ORDER BY created_at DESC LIMIT 500")


def is_note(name: str) -> bool:
    """队员自己的工作笔记：新名字「整理成果-报告撰写员.md」（末尾是队员名），老名字「A-整理成果.md」。
    交付时笔记不单独列给主人，除非没有更正式的东西。"""
    import re
    from pathlib import Path
    if re.match(r"^[A-Z]-", name or ""):
        return True
    stem = Path(name or "").stem
    if "-" not in stem:
        return False
    from .workers import registry
    tail = stem.rsplit("-", 1)[1]
    return any(tail == c.name.replace(" ", "") for c in registry.all_cards())
