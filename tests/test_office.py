"""PPT / Word / Excel / PDF：队员用工具直接生成真文件，能读回来、能在右栏预览；做 PPT 这类长文档先跟主人对大纲。"""
import asyncio
import time

import httpx
import pytest

from server import db, docview, graph, llm, settings, uploads
from server.main import app
from server.tools import Ctx, office


@pytest.fixture
def ctx(fresh_db, tmp_path):
    db.insert("tasks", id="t", parent_id=None, label="", title="文档", goal="g", status="running",
              created_at=db.now(), updated_at=db.now())
    return Ctx("t", "t", "w", tmp_path)


async def test_make_pptx_and_preview(ctx, tmp_path):
    out = await office.make_pptx(ctx, "演示/自我介绍", "Business English Self-Introduction", [
        {"title": "About Me", "bullets": ["Name: Li Ming", "Product manager at Acme"], "notes": "开场先打招呼"},
        {"title": "Strengths", "layout": "two_col", "left_title": "Skills", "left": ["AI products"],
         "right_title": "Habits", "right": ["Reading"]},
        {"title": "Motto", "layout": "quote", "text": "Keep learning"}], subtitle="2026", ending="Thank you")
    p = tmp_path / "演示/自我介绍.pptx"
    assert "5 页" in out and p.exists(), "封面 + 3 页 + 结束页"
    text = uploads.extract(p)
    assert "Li Ming" in text and "开场先打招呼" in text and "Keep learning" in text
    h = docview.render(p)
    assert h.count('class="s') == 5 and "讲稿：开场先打招呼" in h


async def test_make_docx_xlsx_pdf(ctx, tmp_path):
    await office.make_docx(ctx, "通知/会议通知.docx", "关于召开党代会的通知", [
        {"type": "p", "text": "各党支部："}, {"type": "h2", "text": "一、会议时间"}, {"type": "bullets", "items": ["11 月 25 日上午 9 点"]},
        {"type": "table", "rows": [["议程", "负责人"], ["工作报告", "【待定】"]]}, {"type": "right", "text": "党委办公室"}])
    t = uploads.extract(tmp_path / "通知/会议通知.docx")
    assert "11 月 25 日上午 9 点" in t and "工作报告" in t and "党委办公室" in t
    await office.make_xlsx(ctx, "表/定价.xlsx", [{"name": "对比", "rows": [["模型", "输入价"], ["DeepSeek", 2], ["Kimi", 4]]}])
    assert "DeepSeek | 2" in uploads.extract(tmp_path / "表/定价.xlsx")
    await office.make_pdf(ctx, "报告/周报.pdf", "# 周报\n\n- 完成了 **三件事**\n\n| 项 | 状态 |\n|---|---|\n| A | 完成 |")
    pdf = tmp_path / "报告/周报.pdf"
    assert pdf.read_bytes()[:4] == b"%PDF" and "三件事" in uploads.extract(pdf)
    assert len(db.q("SELECT * FROM artifacts")) == 3, "Word、Excel、PDF 都记成产物"


def test_needs_outline():
    assert graph.needs_outline("我想要做一份PPT，关于商务英语的自我介绍")
    assert graph.needs_outline("写一份智能眼镜的调研报告")
    assert not graph.needs_outline("写一份会议通知，明天开党代会")
    assert not graph.needs_outline("开发一个贪吃蛇小游戏")
    assert not graph.needs_outline("用这组数据给我做个能直接打开看的图表报告"), "给了数据的图表报告不用对大纲"
    assert not graph.needs_outline("帮我写一个公众号文章的开头，三段话"), "几段话不用对大纲"
    assert graph.needs_outline("写一篇公众号文章，讲我怎么做 Yip")


async def test_ppt_outline_first(fresh_db, monkeypatch):
    """做 PPT：先把大纲发给主人，说开工才派人，确认过的大纲交给队员照着做。"""
    async def no_model(*a, **k):
        raise llm.LLMError("不调真模型")

    async def fake_chat(msgs, **k):
        if "大纲" in msgs[0]["content"]:
            return "按这些来：面试用、英文、8 页\n1. 封面\n2. 我是谁"
        raise llm.LLMError("不调真模型")
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(settings, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "chat_json", no_model)
    monkeypatch.setattr(llm, "chat_tools", no_model)
    monkeypatch.setattr(graph, "rule_plan", lambda goal: [{"title": "做 PPT", "goal": goal, "steps": 3, "after": []}])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            mid = (await c.post("/api/tasks", json={"goal": "我想要做一份PPT，关于商务英语的自我介绍"})).json()["id"]
            t0 = time.monotonic()
            while (db.task(mid) or {}).get("note") != "等你点头" and time.monotonic() - t0 < 10:
                await asyncio.sleep(0.05)
            assert db.task(mid)["note"] == "等你点头" and not db.children(mid), "先对大纲，不派人"
            last = db.q("SELECT content FROM messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (mid,))[0]
            assert "1. 封面" in last["content"] and "开工" in last["content"]
            r = (await c.post("/api/say", json={"text": "开工"})).json()
            assert r["kind"] == "go"
            while not db.children(mid) and time.monotonic() - t0 < 10:
                await asyncio.sleep(0.05)
            kid = db.children(mid)[0]
            assert any("1. 封面" in x for x in kid["constraints"]), "队员照确认过的大纲做"
            assert kid["accept"][0].startswith("页数、顺序和每页内容跟主人确认过的大纲一致")
            await c.post(f"/api/tasks/{mid}/cancel")


async def test_docx_text_keeps_table_in_place(ctx, tmp_path):
    await office.make_docx(ctx, "n.docx", "通知", [{"type": "h2", "text": "议程"}, {"type": "table", "rows": [["议程", "人"], ["报告", "甲"]]},
                                                  {"type": "right", "text": "党委办公室"}])
    t = uploads.extract(tmp_path / "n.docx")
    assert t.index("报告 | 甲") < t.index("党委办公室"), "表格在落款前面"
