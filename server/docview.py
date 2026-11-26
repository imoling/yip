"""在右栏里看 Word / Excel：转成一页简单的 HTML，排版尽量像原件（标题、居中、加粗、表格）。"""
from __future__ import annotations

import html
from pathlib import Path

CSS = """<style>
body{margin:0;background:#f2f2f0;font:15px/1.8 "PingFang SC","Songti SC",serif;color:#1d1d1f}
.page{max-width:720px;margin:20px auto;background:#fff;padding:56px 64px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
p{margin:0 0 .5em;white-space:pre-wrap}.c{text-align:center}.r{text-align:right}.j{text-align:justify}
h1{font-size:22px;text-align:center;margin:0 0 1em}h2{font-size:18px;margin:1em 0 .5em}h3{font-size:16px;margin:1em 0 .4em}
table{border-collapse:collapse;margin:.6em 0;width:100%}td,th{border:1px solid #ccc;padding:4px 8px;font-size:13px;vertical-align:top}
th{background:#f6f6f4}.sheet{font:13px/1.5 -apple-system,"PingFang SC",sans-serif}.sheet h2{font-size:14px;color:#666}
@media (prefers-color-scheme: dark){body{background:#1c1c1e}}
</style>"""


def _runs(p) -> str:
    out = []
    for r in p.runs:
        t = html.escape(r.text)
        if not t:
            continue
        if r.bold:
            t = f"<b>{t}</b>"
        if r.italic:
            t = f"<i>{t}</i>"
        if r.underline:
            t = f"<u>{t}</u>"
        out.append(t)
    return "".join(out) or html.escape(p.text)


def docx_html(path: Path) -> str:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH as A
    d = Document(str(path))
    body = []
    block = d.element.body
    paras = {id(p._p): p for p in d.paragraphs}
    tables = {id(t._tbl): t for t in d.tables}
    for el in block.iterchildren():
        if id(el) in paras:
            p = paras[id(el)]
            style = (p.style.name if p.style is not None else "").lower()
            inner = _runs(p)
            if style.startswith("title") or style == "heading 1":
                body.append(f"<h1>{inner}</h1>")
            elif style.startswith("heading"):
                lvl = 2 if style.endswith("2") else 3
                body.append(f"<h{lvl}>{inner}</h{lvl}>")
            else:
                cls = {A.CENTER: "c", A.RIGHT: "r", A.JUSTIFY: "j"}.get(p.alignment, "")
                body.append(f'<p class="{cls}">{inner or "&nbsp;"}</p>')
        elif id(el) in tables:
            rows = "".join("<tr>" + "".join(f"<td>{html.escape(c.text)}</td>" for c in r.cells) + "</tr>"
                           for r in tables[id(el)].rows)
            body.append(f"<table>{rows}</table>")
    return f"<!doctype html><meta charset=utf-8>{CSS}<div class=page>{''.join(body)}</div>"


def xlsx_html(path: Path) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets[:6]:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 500:
                break
            tag = "th" if i == 0 else "td"
            rows.append("<tr>" + "".join(f"<{tag}>{'' if v is None else html.escape(str(v))}</{tag}>" for v in row) + "</tr>")
        parts.append(f"<h2>{html.escape(ws.title)}</h2><table>{''.join(rows)}</table>")
    return f"<!doctype html><meta charset=utf-8>{CSS}<div class='page sheet'>{''.join(parts)}</div>"


PPT_CSS = """<style>
body{margin:0;background:#2b2b2e;font:14px/1.6 -apple-system,"PingFang SC",sans-serif;color:#1d1d1f;padding:18px}
.s{position:relative;aspect-ratio:16/9;max-width:860px;margin:0 auto 6px;background:#fff;border-radius:6px;overflow:hidden;
box-shadow:0 2px 10px rgba(0,0,0,.3);padding:5% 6%;box-sizing:border-box}
.s.cover{background:#2f7d5b;color:#fff;display:flex;flex-direction:column;justify-content:center}
.s h2{margin:0 0 3%;font-size:clamp(16px,3vw,28px)}.s.cover h2{font-size:clamp(20px,4vw,38px)}
.s p{margin:0 0 1.2%;font-size:clamp(11px,1.7vw,17px)}
.n{max-width:860px;margin:0 auto 18px;color:#c8c8cc;font-size:12.5px;white-space:pre-wrap}
.i{position:absolute;right:12px;bottom:8px;font-size:11px;color:#9a9a9e}
</style>"""


def pptx_html(path: Path) -> str:
    """PPT 一页一页排出来（文字和讲稿）；版式按 16:9 卡片近似显示，精确效果用系统应用打开看。"""
    from pptx import Presentation
    prs = Presentation(str(path))
    out = []
    for i, sl in enumerate(prs.slides, 1):
        texts = [sh.text_frame.text for sh in sl.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
        head, rest = (texts[0], texts[1:]) if texts else ("", [])
        lines = "".join(f"<p>{html.escape(t)}</p>" for blk in rest for t in blk.split("\n") if t.strip())
        cover = " cover" if i == 1 else ""
        note = sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""
        out.append(f'<div class="s{cover}"><h2>{html.escape(head)}</h2>{lines}<span class="i">{i}</span></div>'
                   + (f'<div class="n">讲稿：{html.escape(note)}</div>' if note.strip() else '<div class="n"></div>'))
    return f"<!doctype html><meta charset=utf-8>{PPT_CSS}{''.join(out)}"


def render(path: Path) -> str | None:
    suf = path.suffix.lower()
    if suf == ".pptx":
        return pptx_html(path)
    if suf == ".docx":
        return docx_html(path)
    if suf in (".xlsx", ".xlsm"):
        return xlsx_html(path)
    return None
