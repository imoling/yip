"""附件：存进工作文件夹、抽文字、跟着一句话进对话或派活；图片在主模型看不了时降级。"""
import io

import httpx
import pytest
from docx import Document
from openpyxl import Workbook

from server import db, llm, settings, uploads
from server.main import app


@pytest.fixture
async def c(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as cl:
            yield cl


def _docx() -> bytes:
    d = Document()
    d.add_paragraph("合同第七条：违约金为合同额的 30%")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _xlsx() -> bytes:
    wb = Workbook()
    wb.active.append(["产品", "价格"])
    wb.active.append(["企业版", 98])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


async def test_upload_extract(c):
    for name, data, want in [("合同.docx", _docx(), "违约金"), ("报价.xlsx", _xlsx(), "企业版 | 98"),
                             ("说明.md", "# 标题\n正文".encode(), "正文")]:
        r = (await c.post("/api/uploads", files={"file": (name, data)})).json()
        assert r["has_text"] and r["kind"] == "file"
        u = uploads.get(r["id"])
        assert want in u["text"] and str(settings.workspace()) in u["path"] and "附件" in u["path"]
    img = (await c.post("/api/uploads", files={"file": ("截图.png", b"\x89PNG\r\n\x1a\nxx", "image/png")})).json()
    assert img["kind"] == "image"
    assert (await c.get(f"/api/uploads/{img['id']}/raw")).status_code == 200
    dup = (await c.post("/api/uploads", files={"file": ("截图.png", b"\x89PNG", "image/png")})).json()
    assert dup["name"] != img["name"], "同名不覆盖"


async def test_say_with_file_chat_and_job(c):
    f = (await c.post("/api/uploads", files={"file": ("合同.docx", _docx())})).json()
    r = (await c.post("/api/say", json={"text": "", "attachments": [f["id"]]})).json()
    assert r["kind"] == "chat"
    m = db.q("SELECT * FROM messages WHERE role='user'")[-1]
    assert m["meta"]["attachments"][0]["name"].startswith("合同")
    r = (await c.post("/api/say", json={"text": "帮我整理一份这份合同的风险清单", "attachments": [f["id"]]})).json()
    assert r["kind"] == "new"
    main = db.task(r["job"])
    assert "违约金" in main["goal"] and "附件/" in main["goal"], "派活时附件内容和路径进目标"
    msg = db.q("SELECT * FROM messages WHERE task_id=? AND role='user'", (r["job"],))[0]
    assert msg["content"] == "帮我整理一份这份合同的风险清单", "对话里只显示你说的那句"


async def test_image_falls_back(c, monkeypatch):
    calls = []

    async def fake_chat(msgs, **kw):
        calls.append(kw.get("override"))
        if kw.get("override"):
            return "图里是一张报价表：企业版 98 元"
        raise llm.LLMError("HTTP 400: image not supported")

    monkeypatch.setattr(llm, "chat", fake_chat)
    img = uploads.get((await c.post("/api/uploads", files={"file": ("a.png", b"\x89PNG", "image/png")})).json()["id"])
    assert await uploads.describe([img]) == "", "没配看图模型就放弃"
    monkeypatch.setattr(settings, "resolve_vision", lambda data=None: {"base_url": "http://x", "api_key": "", "model": "qwen-vl"})
    assert "企业版" in await uploads.describe([img])
    assert calls[-1]["model"] == "qwen-vl"
