"""工作文件夹里的读写：路径一律解析到工作文件夹内，越界直接拒绝。写出的文件记成产物。"""
from __future__ import annotations

from pathlib import Path

from .. import artifacts
from . import READ, WRITE, Ctx, Tool, ToolError, clip, obj

TEXT_EXT = {".md", ".txt", ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml", ".py", ".js", ".ts", ".tsv", ".log"}


def skill_roots() -> list[Path]:
    """技能目录：技能里的参考资料和脚本只读放行。"""
    from .. import db
    from ..workers import registry
    from . import skills
    return [skills.builtin_dir().resolve(), (db.DATA_DIR / "skills").resolve(), registry.BUILTIN_DIR.resolve(),
            (db.DATA_DIR / "workers").resolve()]


def inside(ctx: Ctx, rel: str, read: bool = False) -> Path:
    ws = ctx.workspace
    ws.mkdir(parents=True, exist_ok=True)
    rel = (rel or ".").strip()
    p = (ws / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    if p != ws and ws not in p.parents:
        if read and any(r in p.parents for r in skill_roots()):
            return p
        raise ToolError(f"只能读写工作文件夹 {ws} 里的文件")
    return p


async def list_dir(ctx: Ctx, path: str = ".") -> str:
    p = inside(ctx, path, read=True)
    if not p.exists():
        return f"（还没有「{path}」这个文件夹，写文件时会自动创建）"
    if p.is_file():
        return f"{p.name}（文件，{p.stat().st_size} 字节）"
    rows = []
    for c in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))[:200]:
        if c.name.startswith("."):
            continue
        rows.append(f"{c.name}/" if c.is_dir() else f"{c.name}  {c.stat().st_size} 字节")
    return "\n".join(rows) or "（空文件夹）"


async def read_file(ctx: Ctx, path: str) -> str:
    p = inside(ctx, path, read=True)
    if not p.is_file():
        raise ToolError(f"没有这个文件：{path}")
    if p.suffix.lower() in (".docx", ".xlsx", ".xlsm", ".pptx", ".pdf"):  # Office / PDF：抽出文字给你看
        from .. import uploads
        return clip(uploads.extract(p) or f"{p.name} 抽不出文字", 20000)
    if p.suffix.lower() not in TEXT_EXT and p.stat().st_size > 0:
        head = p.read_bytes()[:2048]
        if b"\x00" in head:
            raise ToolError(f"{p.name} 不是文本文件，读不了")
    return clip(p.read_text("utf-8", errors="replace"), 20000)


async def write_file(ctx: Ctx, path: str, content: str) -> str:
    p = inside(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "", "utf-8")
    artifacts.record(ctx, p)
    return f"已写入 {p.relative_to(ctx.workspace)}（{p.stat().st_size} 字节）"


async def edit_file(ctx: Ctx, path: str, old: str, new: str) -> str:
    p = inside(ctx, path)
    if not p.is_file():
        raise ToolError(f"没有这个文件：{path}")
    text = p.read_text("utf-8")
    n = text.count(old)
    if n == 0:
        raise ToolError("没找到要替换的原文")
    if n > 1:
        raise ToolError(f"原文出现了 {n} 次，多带点上下文让它唯一")
    p.write_text(text.replace(old, new, 1), "utf-8")
    artifacts.record(ctx, p)
    return f"已修改 {p.relative_to(ctx.workspace)}"


def _size(a: dict) -> str:
    n = len((a.get("content") or "").encode())
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} 字节"


TOOLS = [
    Tool("list_dir", "列出工作文件夹里某个目录的内容，路径相对工作文件夹。",
         obj({"path": {"type": "string", "description": "相对路径，默认 ."}}, []),
         list_dir, READ, lambda a: f"看文件夹 {a.get('path') or '.'}", "看文件夹"),
    Tool("read_file", "读取工作文件夹里的文本文件。",
         obj({"path": {"type": "string", "description": "相对工作文件夹的路径"}}),
         read_file, READ, lambda a: f"读取 {a.get('path', '')}", "读取"),
    Tool("write_file", "在工作文件夹里写一个文件（新建或整个覆盖）。交付物用它落盘，Markdown 优先。",
         obj({"path": {"type": "string", "description": "相对工作文件夹的路径，如 竞品简报/定价.md"},
              "content": {"type": "string", "description": "完整文件内容"}}),
         write_file, WRITE, lambda a: f"写入 {a.get('path', '')} · {_size(a)}", "写入"),
    Tool("edit_file", "替换工作文件夹里某个文件中唯一的一段原文。",
         obj({"path": {"type": "string"}, "old": {"type": "string", "description": "要替换的原文，必须唯一"},
              "new": {"type": "string", "description": "替换成"}}),
         edit_file, WRITE, lambda a: f"修改 {a.get('path', '')}", "修改"),
]
