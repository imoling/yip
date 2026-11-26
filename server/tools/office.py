"""PPT / Word / Excel / PDF：队员给结构化的内容，这里排好版生成真文件，不用自己写一长段脚本（写长了模型容易超时）。

- make_pptx：16:9，封面 + 内容页（要点 / 左右两栏 / 金句）+ 可选结束页，讲稿写进备注
- make_docx：标题、一二级标题、段落、要点、表格
- make_xlsx：几个工作表，首行加粗、冻结、列宽按内容
- make_pdf：Markdown 排成网页，用本机 Chrome 打印成 PDF
读这些文件用 read_file（会抽出文字）。颜色和 Yip 界面一致：墨绿主色，不用紫色。
"""
from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path

from .. import artifacts
from . import WRITE, Ctx, Tool, ToolError, obj
from .files import inside

ACCENT = (0x2F, 0x7D, 0x5B)
INK = (0x1D, 0x1D, 0x1F)
MUTED = (0x6E, 0x6E, 0x73)
FONT = "PingFang SC"


def _target(ctx: Ctx, path: str, suf: str) -> Path:
    if str(path).startswith(str(ctx.workspace)):
        path = str(path)[len(str(ctx.workspace)):].lstrip("/")
    p = inside(ctx, path if path.lower().endswith(suf) else path + suf)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _done(ctx: Ctx, p: Path, what: str) -> str:
    artifacts.record(ctx, p)
    return f"已生成 {p.relative_to(ctx.workspace)}（{what}，{max(1, p.stat().st_size // 1024)} KB）"


# ---------- PPT ----------
def _pptx(p: Path, title: str, subtitle: str, slides: list[dict], ending: str) -> int:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # 16:9
    W, H = prs.slide_width, prs.slide_height
    blank = prs.slide_layouts[6]

    def box(slide, x, y, w, h, text, size, color=INK, bold=False, align=None):
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        lines = text if isinstance(text, list) else [text]
        for i, line in enumerate(lines):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            run = para.add_run()
            run.text = str(line)
            run.font.size, run.font.bold, run.font.name = Pt(size), bold, FONT
            run.font.color.rgb = RGBColor(*color)
            if align is not None:
                para.alignment = align
            para.space_after = Pt(size * 0.6)
        return tb

    def bar(slide):
        s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(0), Emu(0), Emu(180000), H)
        s.fill.solid()
        s.fill.fore_color.rgb = RGBColor(*ACCENT)
        s.line.fill.background()

    def notes(slide, text):
        if text:
            slide.notes_slide.notes_text_frame.text = str(text)

    from pptx.enum.text import PP_ALIGN
    s = prs.slides.add_slide(blank)  # 封面
    bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(0), Emu(0), W, H)
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor(*ACCENT)
    bg.line.fill.background()
    box(s, Emu(900000), Emu(2300000), W - Emu(1800000), Emu(1400000), title, 40, (0xFF, 0xFF, 0xFF), True)
    if subtitle:
        box(s, Emu(900000), Emu(3700000), W - Emu(1800000), Emu(800000), subtitle, 20, (0xE3, 0xEF, 0xE8))

    for sl in slides:
        s = prs.slides.add_slide(blank)
        bar(s)
        kind = sl.get("layout") or "bullets"
        box(s, Emu(700000), Emu(450000), W - Emu(1400000), Emu(900000), sl.get("title") or "", 30, INK, True)
        body_top, body_h = Emu(1500000), H - Emu(2100000)
        if kind == "quote":
            box(s, Emu(1200000), Emu(2200000), W - Emu(2400000), Emu(2400000), sl.get("text") or "", 28, ACCENT, True,
                PP_ALIGN.CENTER)
        elif kind == "two_col":
            half = (W - Emu(1700000)) // 2
            for i, (k, items) in enumerate([("left", sl.get("left") or []), ("right", sl.get("right") or [])]):
                x = Emu(700000) + i * (half + Emu(300000))
                head = (sl.get(f"{k}_title") or "").strip()
                if head:
                    box(s, x, body_top, half, Emu(500000), head, 20, ACCENT, True)
                box(s, x, body_top + (Emu(600000) if head else 0), half, body_h, [f"• {t}" for t in items], 18)
        else:
            items = sl.get("bullets") or []
            size = 22 if len(items) <= 4 else 18 if len(items) <= 7 else 16
            box(s, Emu(700000), body_top, W - Emu(1400000), body_h, [f"• {t}" for t in items], size)
        notes(s, sl.get("notes"))

    if ending:
        s = prs.slides.add_slide(blank)
        bar(s)
        box(s, Emu(700000), Emu(2600000), W - Emu(1400000), Emu(1200000), ending, 36, ACCENT, True, PP_ALIGN.CENTER)
    prs.save(str(p))
    return len(prs.slides)


async def make_pptx(ctx: Ctx, path: str, title: str, slides: list, subtitle: str = "", ending: str = "") -> str:
    if not slides:
        raise ToolError("slides 至少要有一页")
    p = _target(ctx, path, ".pptx")
    n = await asyncio.to_thread(_pptx, p, title, subtitle, [x for x in slides if isinstance(x, dict)], ending)
    return _done(ctx, p, f"{n} 页")


# ---------- Word ----------
def _docx(p: Path, title: str, blocks: list[dict]) -> int:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = FONT, Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    if title:
        h = d.add_paragraph()
        r = h.add_run(title)
        r.bold, r.font.size, r.font.color.rgb = True, Pt(20), RGBColor(*INK)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for b in blocks:
        t = (b.get("type") or "p").lower()
        if t in ("h1", "h2", "h3"):
            hd = d.add_heading(b.get("text") or "", level=int(t[1]))
            for r in hd.runs:
                r.font.name = FONT
                r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
                r.font.color.rgb = RGBColor(*ACCENT) if t == "h1" else RGBColor(*INK)
        elif t == "bullets":
            for it in b.get("items") or []:
                d.add_paragraph(str(it), style="List Bullet")
        elif t == "numbers":
            for it in b.get("items") or []:
                d.add_paragraph(str(it), style="List Number")
        elif t == "table":
            rows = [list(map(str, r)) for r in b.get("rows") or [] if isinstance(r, (list, tuple))]
            if rows:
                tb = d.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                tb.style = "Table Grid"
                for i, r in enumerate(rows):
                    for j, v in enumerate(r):
                        tb.cell(i, j).text = v
                        if i == 0:
                            for run in tb.cell(i, j).paragraphs[0].runs:
                                run.bold = True
        elif t == "right":  # 落款、日期这类靠右
            para = d.add_paragraph(b.get("text") or "")
            para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        else:
            d.add_paragraph(b.get("text") or "")
    d.save(str(p))
    return len(blocks)


async def make_docx(ctx: Ctx, path: str, title: str, blocks: list) -> str:
    if not blocks:
        raise ToolError("blocks 至少要有一段")
    p = _target(ctx, path, ".docx")
    await asyncio.to_thread(_docx, p, title, [x for x in blocks if isinstance(x, dict)])
    return _done(ctx, p, f"{len(blocks)} 段")


# ---------- Excel ----------
def _xlsx(p: Path, sheets: list[dict]) -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet((sh.get("name") or f"表{len(wb.sheetnames) + 1}")[:31])
        rows = [r for r in sh.get("rows") or [] if isinstance(r, (list, tuple))]
        for r in rows:
            ws.append(list(r))
        if rows and sh.get("header", True):
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="2F7D5B")
            ws.freeze_panes = "A2"
        for i, col in enumerate(zip(*rows) if rows else [], 1):
            width = max(len(str(v)) * (2 if re.search(r"[一-鿿]", str(v)) else 1) for v in col if v is not None)
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)
    wb.save(str(p))
    return len(sheets)


async def make_xlsx(ctx: Ctx, path: str, sheets: list) -> str:
    if not sheets:
        raise ToolError("sheets 至少要有一个工作表")
    p = _target(ctx, path, ".xlsx")
    n = await asyncio.to_thread(_xlsx, p, [x for x in sheets if isinstance(x, dict)])
    return _done(ctx, p, f"{n} 个工作表")


# ---------- PDF ----------
def md_html(md: str, title: str = "") -> str:
    """够用的 Markdown 转网页：标题、段落、要点、编号、表格、加粗。"""
    out, lines, i = [], md.splitlines(), 0

    def inline(s: str) -> str:
        s = html.escape(s)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
            i += 1
        elif re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(f"<li>{inline(re.sub(r'^\s*[-*]\s+', '', lines[i]))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif re.match(r"^\s*\d+[.、]\s*", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+[.、]\s*", lines[i]):
                items.append(f"<li>{inline(re.sub(r'^\s*\d+[.、]\s*', '', lines[i]))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
        elif ln.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            body = "".join("<tr>" + "".join(f"<{'th' if k == 0 else 'td'}>{inline(c)}</{'th' if k == 0 else 'td'}>"
                                             for c in r) + "</tr>" for k, r in enumerate(rows))
            out.append(f"<table>{body}</table>")
        else:
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\s*[-*]\s|\s*\d+[.、]|\|)", lines[i]):
                para.append(lines[i].rstrip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    css = ("body{font:12pt/1.7 'PingFang SC',sans-serif;color:#1d1d1f;margin:0}h1{font-size:20pt;color:#2f7d5b}"
           "h2{font-size:15pt;border-bottom:1px solid #e3e3e3;padding-bottom:4px}table{border-collapse:collapse;width:100%}"
           "td,th{border:1px solid #ccc;padding:4px 8px;font-size:10.5pt}th{background:#e3efe8}")
    return f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title><style>{css}</style>{''.join(out)}"


async def make_pdf(ctx: Ctx, path: str, markdown: str, title: str = "") -> str:
    from playwright.async_api import async_playwright

    from .browser import CHROME
    if not markdown.strip():
        raise ToolError("markdown 是空的")
    p = _target(ctx, path, ".pdf")
    page_html = md_html(markdown, title)
    async with async_playwright() as pw:
        kw = {"executable_path": CHROME} if Path(CHROME).exists() else {}
        b = await pw.chromium.launch(headless=True, **kw)
        try:
            pg = await b.new_page()
            await pg.set_content(page_html, wait_until="load")
            await pg.pdf(path=str(p), format="A4", margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
                         print_background=True)
        finally:
            await b.close()
    return _done(ctx, p, "A4")


PPT_SLIDE = {"type": "object", "properties": {
    "title": {"type": "string"},
    "layout": {"type": "string", "enum": ["bullets", "two_col", "quote"], "description": "默认 bullets"},
    "bullets": {"type": "array", "items": {"type": "string"}, "description": "要点，每页 3 到 6 条，短句"},
    "left_title": {"type": "string"}, "left": {"type": "array", "items": {"type": "string"}},
    "right_title": {"type": "string"}, "right": {"type": "array", "items": {"type": "string"}},
    "text": {"type": "string", "description": "quote 页的一句话"},
    "notes": {"type": "string", "description": "这一页的讲稿，写进备注"}}, "required": ["title"]}
DOC_BLOCK = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["h1", "h2", "h3", "p", "bullets", "numbers", "table", "right"]},
    "text": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "表格，第一行是表头"}},
    "required": ["type"]}

TOOLS = [
    Tool("make_pptx", "生成 PPT（.pptx，16:9，排好版）：给标题和每页的要点 / 讲稿就行，不要自己写脚本。路径相对工作文件夹",
         obj({"path": {"type": "string", "description": "比如 商务英语自我介绍/自我介绍.pptx"},
              "title": {"type": "string"}, "subtitle": {"type": "string"},
              "slides": {"type": "array", "items": PPT_SLIDE}, "ending": {"type": "string", "description": "结束页的一句话，可不填"}},
             ["path", "title", "slides"]),
         make_pptx, WRITE, lambda a: f"生成 PPT {a.get('path', '')} · {len(a.get('slides') or [])} 页", "生成 PPT"),
    Tool("make_docx", "生成 Word（.docx）：仅当主人或技能明确要求 Word/docx 时使用（普通报告和对比优先写 Markdown，不要额外生成 Word）；按顺序给标题、段落、要点、表格、靠右的落款，不要自己写脚本",
         obj({"path": {"type": "string"}, "title": {"type": "string"}, "blocks": {"type": "array", "items": DOC_BLOCK}},
             ["path", "title", "blocks"]),
         make_docx, WRITE, lambda a: f"生成 Word {a.get('path', '')}", "生成 Word"),
    Tool("make_xlsx", "生成 Excel（.xlsx）：几个工作表，每个给行（第一行是表头）",
         obj({"path": {"type": "string"}, "sheets": {"type": "array", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "rows": {"type": "array", "items": {"type": "array"}}}, "required": ["rows"]}}},
             ["path", "sheets"]),
         make_xlsx, WRITE, lambda a: f"生成 Excel {a.get('path', '')}", "生成 Excel"),
    Tool("make_pdf", "把 Markdown 排版成 PDF（A4）", obj({"path": {"type": "string"}, "markdown": {"type": "string"},
                                                    "title": {"type": "string"}}, ["path", "markdown"]),
         make_pdf, WRITE, lambda a: f"生成 PDF {a.get('path', '')}", "生成 PDF"),
]
