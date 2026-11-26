"""对话附件：图片和文件。

存到「工作文件夹/附件/<日期>/」，队员用 read_file 就能读原件；能抽文字的（txt、md、csv、json、docx、xlsx、pdf）
先抽好存库里，拼进上下文。图片按 OpenAI 多模态格式交给模型；主模型看不了时用「看图模型」读成文字。
"""
from __future__ import annotations

import base64
import mimetypes
import re
import time
from pathlib import Path

from . import db, llm, settings

TEXT_MAX = 20000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".html", ".htm", ".xml", ".yaml", ".yml", ".py", ".js", ".ts"}

SCHEMA = """CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, name TEXT NOT NULL, mime TEXT, size INTEGER,
  kind TEXT, text TEXT, created_at INTEGER NOT NULL)"""


def _ensure() -> None:
    db.ex(SCHEMA)


def kind_of(name: str, mime: str = "") -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT or (mime or "").startswith("image/"):
        return "image"
    return "file"


def extract(path: Path) -> str:
    """能抽就抽，抽不出返回空串；永远不抛。"""
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXT:
            return path.read_text("utf-8", errors="replace")[:TEXT_MAX]
        if ext == ".docx":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            d = Document(str(path))
            parts = []
            for el in d.element.body.iterchildren():  # 按正文顺序：段落和表格交错，表格不挪到最后
                tag = el.tag.rsplit("}", 1)[-1]
                if tag == "p":
                    t = Paragraph(el, d).text
                    if t.strip():
                        parts.append(t)
                elif tag == "tbl":
                    for r in Table(el, d).rows:
                        parts.append(" | ".join(c.text.strip() for c in r.cells))
            return "\n".join(parts)[:TEXT_MAX]
        if ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(str(path), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets[:5]:
                out.append(f"## {ws.title}")
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 200:
                        out.append("…")
                        break
                    out.append(" | ".join("" if v is None else str(v) for v in row))
            return "\n".join(out)[:TEXT_MAX]
        if ext == ".pptx":
            from pptx import Presentation
            out = []
            for i, sl in enumerate(Presentation(str(path)).slides, 1):
                texts = [sh.text_frame.text for sh in sl.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
                note = sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""
                out.append(f"## 第 {i} 页\n" + "\n".join(texts) + (f"\n（讲稿）{note}" if note.strip() else ""))
            return "\n\n".join(out)[:TEXT_MAX]
        if ext == ".pdf":
            from pypdf import PdfReader
            r = PdfReader(str(path))
            return "\n".join((p.extract_text() or "") for p in r.pages[:40])[:TEXT_MAX]
    except Exception:  # noqa: BLE001
        return ""
    return ""


def save(name: str, data: bytes, mime: str = "") -> dict:
    _ensure()
    name = re.sub(r'[\\/:*?"<>|]', "_", Path(name or "附件").name) or "附件"
    folder = settings.workspace() / "附件" / time.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    n = 1
    while dest.exists():
        dest = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    dest.write_bytes(data)
    mime = mime or mimetypes.guess_type(name)[0] or ""
    kind = kind_of(name, mime)
    text = "" if kind == "image" else extract(dest)
    uid = db.insert("uploads", path=str(dest), name=dest.name, mime=mime, size=len(data), kind=kind, text=text,
                    created_at=db.now())
    return public(get(uid))


def get(uid: int) -> dict | None:
    _ensure()
    return db.q1("SELECT * FROM uploads WHERE id=?", (uid,))


def public(u: dict | None) -> dict | None:
    if not u:
        return None
    return {"id": u["id"], "name": u["name"], "kind": u["kind"], "size": u["size"], "mime": u["mime"],
            "path": u["path"], "has_text": bool(u.get("text"))}


def many(ids: list) -> list[dict]:
    out = []
    for i in ids or []:
        try:
            u = get(int(i))
        except (TypeError, ValueError):
            continue
        if u:
            out.append(u)
    return out


def rel(u: dict) -> str:
    try:
        return str(Path(u["path"]).relative_to(settings.workspace()))
    except ValueError:
        return u["path"]


def text_block(items: list[dict], limit: int = 6000) -> str:
    """文件附件拼成一段文字：路径（队员可 read_file）+ 抽出来的内容（截断），过安全防线检查。"""
    from . import sentinel
    parts = []
    for u in items:
        if u["kind"] == "image":
            continue
        body = (u.get("text") or "").strip()
        if body:
            body, _ = sentinel.wrap(body[:limit], source=f"附件《{u['name']}》")
        head = f"附件《{u['name']}》（工作文件夹里的 {rel(u)}）"
        parts.append(f"{head}\n{body}" + ("\n…（后面还有，用 read_file 看全文）" if len((u.get("text") or "").strip()) > limit else "")
                     if body else f"{head}：这类文件抽不出文字，需要时用工具读原件")
    return "\n\n".join(parts)


def image_part(u: dict) -> dict:
    data = base64.b64encode(Path(u["path"]).read_bytes()).decode()
    mime = u.get("mime") or "image/png"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


# 模型不报错、但回一句「看不到图」：当成看不了图处理
BLIND = re.compile(r"(看不到|看不了|无法(查看|看到|识别|读取|直接查看|处理)|不能(查看|看到|识别)|不支持(的)?(图片|图像)|没有(看到|收到)(任何)?(图片|图像)"
                   r"|cannot (see|view)|can't (see|view)|unable to (see|view)|don't see (an|any) image)", re.I)


def blind(answer: str) -> bool:
    """短回复里说自己看不到图。长回复只看开头，免得正文里提一句就误判。"""
    return bool(answer) and bool(BLIND.search(answer[:200]))


def eyes(main: bool = True, daily_first: bool = False) -> list[str]:
    """能试着看图的模型，按顺序：难活模型、日常模型（DeepSeek 的 flash 能看图，v4-pro 不能）、设置里的看图模型。
    daily_first：聊天这类本来就用日常模型的，先让它看。"""
    out = ["main"] if main else []
    if settings.load().get("fast_model"):
        out.insert(0, "fast") if daily_first else out.append("fast")
    if settings.resolve_vision():
        out.append("vision")
    return out


async def look(msgs: list[dict], *, main: bool = True, daily_first: bool = False, timeout: float = 90,
               temperature: float = 0.2) -> str:
    """带图片的对话按顺序换模型试，第一个真看到图的回答为准；都看不了返回空串。"""
    for who in eyes(main, daily_first):
        try:
            got = await llm.chat(msgs, json_mode=False, timeout=timeout, temperature=temperature,
                                 fast=who == "fast", override=settings.resolve_vision() if who == "vision" else None)
        except Exception:  # noqa: BLE001 — 这个模型不收图，换下一个
            continue
        if got and not blind(got):
            return got
    return ""


async def describe(images: list[dict], question: str = "", *, main: bool = True) -> str:
    """把图片读成文字。按 eyes() 的顺序换模型试；都不行返回空串。"""
    if not images:
        return ""
    content = [{"type": "text", "text": (
        "把下面的图片里有用的信息完整读出来：文字照录，表格转成 Markdown，图表说清数据和趋势，界面截图说清在哪个页面、有什么。"
        + (f"\n主人想问：{question}" if question else ""))}]
    content += [image_part(u) for u in images]
    return await look([{"role": "user", "content": content}], main=main)
    content = [{"type": "text", "text": (
        "把下面的图片里有用的信息完整读出来：文字照录，表格转成 Markdown，图表说清数据和趋势，界面截图说清在哪个页面、有什么。"
        + (f"\n主人想问：{question}" if question else ""))}]
    content += [image_part(u) for u in images]
    msgs = [{"role": "user", "content": content}]
    for fast in (("main", "vision") if main else ("vision",)):
        if fast == "vision" and not settings.resolve_vision():
            break
        try:
            override = settings.resolve_vision() if fast == "vision" else None
            got = await llm.chat(msgs, json_mode=False, timeout=90, override=override)
            if not blind(got):
                return got
        except Exception:  # noqa: BLE001 — 这个模型看不了图，换下一个
            continue
    return ""
