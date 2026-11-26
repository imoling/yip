"""接力拆活、DeepSeek 文本工具调用、看图拒答：这一轮「贪吃蛇做不出来」暴露的三个问题。"""
import asyncio
import time

import httpx
import pytest

from server import db, graph, llm, uploads
from server.main import app


@pytest.fixture
async def client(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def wait(pred, timeout=20.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_relay_runs_in_order_and_chat_stays_quiet(client, monkeypatch):
    monkeypatch.setattr(graph, "rule_plan", lambda goal: [
        {"title": "需求", "goal": "写需求和验收标准", "steps": 3, "after": []},
        {"title": "开发", "goal": "写出能运行的 index.html", "steps": 3, "after": [0]},
        {"title": "测试", "goal": "测试并修好", "steps": 3, "after": [1, 5, 2]},  # 越界、自指都要丢掉
    ])
    r = await client.post("/api/tasks", json={"goal": "开发一个贪吃蛇小游戏"})
    mid = r.json()["id"]
    assert await wait(lambda: len(db.children(mid)) == 3, 5)
    a, b, c = sorted(db.children(mid), key=lambda k: k["label"])
    assert b["prereqs"] == [a["id"]] and c["prereqs"] == [b["id"]]
    assert b["status"] == "waiting" and b["note"].startswith("等需求分析师"), "说队员名，不说 A"
    assert await wait(lambda: db.task(mid)["status"] == "done", 60)
    ends = {k["label"]: k for k in db.children(mid)}
    assert all(k["status"] == "done" for k in ends.values())
    started = {e["task_id"]: e["created_at"] for e in db.q("SELECT * FROM task_events WHERE type='dequeued'")}
    assert b["id"] in started and c["id"] in started
    done_a = db.q1("SELECT created_at FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 1", (a["id"],))["created_at"]
    assert started[b["id"]] >= done_a
    says = [m["content"] for m in db.q("SELECT * FROM messages WHERE task_id=? AND role='assistant'", (mid,))]
    assert says[0].startswith("好，让") and "接着" in says[0] and len(says) <= 4, says
    assert not any("交了 " in s or "子任务" in s for s in says), says


def test_parse_dsml():
    text = ("先看看文件夹。\n<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name=\"list_dir\">\n"
            "<｜｜DSML｜｜parameter name=\"path\" string=\"true\">贪吃蛇</｜｜DSML｜｜parameter>\n"
            "<｜｜DSML｜｜parameter name=\"depth\" string=\"false\">2</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n"
            "<｜｜DSML｜｜invoke name=\"write_file\">\n<｜｜DSML｜｜parameter name=\"content\" string=\"true\"><html>"
            "</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>")
    calls, rest = llm.parse_dsml(text)
    assert rest == "先看看文件夹。"
    assert [c["function"]["name"] for c in calls] == ["list_dir", "write_file"]
    import json
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "贪吃蛇", "depth": 2}
    assert llm.parse_dsml("普通回答")[0] == []


async def test_chat_tools_turns_dsml_into_calls(monkeypatch):
    async def fake_post(s, body, timeout):
        return {"choices": [{"message": {"content": "<|DSML|invoke name=\"read_file\"><|DSML|parameter name=\"path\" "
                                                    "string=\"true\">a.md</|DSML|parameter></|DSML|invoke>"}}]}
    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setattr(llm, "_target", lambda *a: {"model": "x"})
    out = await llm.chat_tools([{"role": "user", "content": "hi"}], [{"type": "function"}])
    assert out["tool_calls"][0]["function"]["name"] == "read_file" and out["content"] == ""


@pytest.mark.parametrize("ans,want", [
    ("抱歉，我无法查看图片内容，请把文字发给我", True),
    ("不支持的图片格式，看不到内容", True),
    ("I'm sorry, I can't see images.", True),
    ("图里是一个贪吃蛇游戏的界面，左上角有分数。", False),
    ("这张截图显示了三栏布局。" + "说明" * 200 + "无法查看", False),
])
def test_blind(ans, want):
    assert uploads.blind(ans) is want


async def test_describe_skips_blind_main(fresh_db, monkeypatch):
    calls = []

    async def fake_chat(msgs, **kw):
        calls.append(kw.get("override"))
        return "抱歉，我看不到图片" if kw.get("override") is None else "图里写着：你好"
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(uploads, "image_part", lambda u: {"type": "text", "text": "img"})
    monkeypatch.setattr(uploads.settings, "resolve_vision", lambda: {"model": "qwen-vl"})
    assert await uploads.describe([{"kind": "image"}], "写了什么") == "图里写着：你好"
    assert calls == [None, {"model": "qwen-vl"}]


# ---------- 带时间的话建成自动化 ----------
def test_schedule_rule_route(fresh_db):
    from server.router import front
    assert front.rule_route("每个工作日的9点给我整理一下邮件，告诉我有哪些事要处理", [])["kind"] == "schedule"
    assert front.rule_route("每周五下午5点汇总本周进展", [])["kind"] == "schedule"
    assert front.rule_route("帮我整理一份三家竞品的简报", [])["kind"] == "new"
    assert front.rule_route("每天几点适合发公众号？", [])["kind"] == "chat"


async def test_say_schedule_creates_automation(client):
    r = await client.post("/api/say", json={"text": "每个工作日的9点给我整理一下邮件，告诉我有哪些事要处理"})
    out = r.json()
    assert out["kind"] == "schedule" and out["automation"]
    a = (await client.get("/api/automations")).json()[0]
    assert a["rule"] == {"type": "weekdays", "time": "09:00"} and "邮件" in a["text"]
    msg = db.q("SELECT * FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1")[0]
    assert msg["meta"]["kind"] == "automation" and "工作日 09:00" in msg["content"]
    assert not db.main_tasks(), "不应该当场派活"


async def test_run_once_after_schedule_starts_job(client):
    """建完自动化后打字说「先跑一次」：真的跑那条自动化，看板上有这件事，不是嘴上答应。"""
    from server.router import front
    assert front.rule_route("先跑一次", [])["kind"] != "run_auto", "没有刚建的自动化时不算"
    await client.post("/api/say", json={"text": "每个工作日 9 点整理下全球最新AI新闻"})
    aid = (await client.get("/api/automations")).json()[0]["id"]
    for t in ("先跑一次", "现在跑一下看看", "那就先跑一次吧"):
        assert front.RUN_ONCE.match(t), t
    out = (await client.post("/api/say", json={"text": "先跑一次"})).json()
    assert out["kind"] == "run_auto" and out["automation"] == aid and out["job"]
    mains = db.main_tasks()
    assert len(mains) == 1 and mains[0]["kind"] == "auto" and "AI" in mains[0]["goal"]
    assert (await client.get("/api/automations")).json()[0]["last_job"] == out["job"]


async def test_view_serves_folder_files(client, tmp_path):
    from types import SimpleNamespace
    from server import artifacts, settings
    d = tmp_path / "贪吃蛇"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<script src='game.js'></script>", "utf-8")
    (d / "game.js").write_text("let x=1", "utf-8")
    aid = artifacts.record(SimpleNamespace(task_id="t", main_id="m", worker_id=None), d / "index.html")
    assert (await client.get(f"/view/-/{aid}/index.html")).status_code == 200
    assert (await client.get(f"/view/-/{aid}/game.js")).text == "let x=1"
    assert (await client.get(f"/view/-/{aid}/../../etc/passwd")).status_code == 404
    assert (await client.get(f"/api/artifacts/{aid}/content")).status_code == 200


async def test_stop_one_member_then_whole_job(client, monkeypatch):
    """进度卡上的「停下」：停一个队员，后面接力的照样能开工；再停整件事，全部收住。"""
    monkeypatch.setattr(graph, "rule_plan", lambda goal: [
        {"title": "开发", "goal": "写 index.html", "steps": 40, "after": []},
        {"title": "测试", "goal": "测试", "steps": 40, "after": [0]},
    ])
    mid = (await client.post("/api/tasks", json={"goal": "做个小工具 " + str(time.time())})).json()["id"]
    assert await wait(lambda: len(db.children(mid)) == 2 and db.children(mid)[0]["status"] == "running", 5)
    a, b = sorted(db.children(mid), key=lambda k: k["label"])
    await client.post(f"/api/tasks/{a['id']}/cancel")
    assert db.task(a["id"])["status"] == "cancelled"
    assert await wait(lambda: db.task(b["id"])["status"] == "running", 5), "停掉 A 后 B 不该一直排着"
    await client.post(f"/api/tasks/{mid}/cancel")
    assert db.task(mid)["status"] == "cancelled" and db.task(b["id"])["status"] == "cancelled"


async def test_cannot_override_old_decision(client):
    """旧判断不能再改：做完的事、后面又插过话的，都拒绝，免得把做完的事推倒重来。"""
    mid = db.new_id()
    db.insert("tasks", id=mid, parent_id=None, label="", title="贪吃蛇", goal="做贪吃蛇", constraints=[], status="done",
              note="", thread_id=f"main:{mid}", created_at=db.now(), updated_at=db.now())
    did = db.insert("decisions", task_id=mid, utterance="算了，做个推箱子吧", intent="overturn", target=mid, reason="",
                    cost="low", confidence=0.95, source="rule", need_confirm=0, progress=0.3, status="done",
                    final_action="overturn", created_at=db.now())
    r = await client.post(f"/api/decisions/{did}/override", json={"intent": "augment"})
    assert r.status_code == 409 and "结束" in r.json()["detail"]
    assert db.task(mid)["status"] == "done"


async def test_image_falls_through_to_fast_model(fresh_db, monkeypatch, tmp_path):
    """DeepSeek：v4-pro 不收图、flash 收。聊天先让日常模型看；验收读图先给难活模型，看不了换 flash。"""
    from server import settings
    from server.router import front
    settings.save({"model": "deepseek:deepseek-v4-pro", "fast_model": "deepseek:deepseek-flash"})
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = []

    async def fake(msgs, *, fast=False, override=None, **k):
        calls.append("fast" if fast else ("vision" if override else "main"))
        return "图里写的是 YIP 7392" if fast else "抱歉，我无法查看这张图片"
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat", fake)
    out = await front.reply("图里写的什么", [{"kind": "image", "path": str(img), "mime": "image/png", "name": "a.png"}])
    assert out == "图里写的是 YIP 7392" and calls[0] == "fast", "聊天先让日常模型看"
    from server import uploads
    calls.clear()
    assert await uploads.describe([{"path": str(img), "mime": "image/png"}]) == "图里写的是 YIP 7392"
    assert calls == ["main", "fast"], "验收读图先给难活模型，看不了换能看图的"
