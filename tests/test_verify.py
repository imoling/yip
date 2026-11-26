"""harness 升级：Yip 用工具决定怎么处理一句话、验收不过退回返工、交付前对照目标查一遍。"""
import asyncio
import json
import time

import httpx
import pytest

from server import db, graph, llm, settings, verify
from server.main import app


async def wait(pred, timeout=30.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.fixture
async def model(fresh_db, monkeypatch):
    """假模型：工具调用由各测试自己给，其余按提示词里的关键词回。"""
    state = {"tools": None, "judge": {"pass": True, "problems": [], "score": 8}, "goal": {"met": True, "gaps": []}}

    async def chat_json(msgs, **k):
        text = msgs[-1]["content"]
        if "你是验收员" in text:
            return state["judge"]
        if "最后一道检查" in text:
            return state["goal"]
        raise llm.LLMError("其余不调模型")

    async def chat(*a, **k):
        raise llm.LLMError("不调")

    async def chat_tools(msgs, tools, **k):
        return await state["tools"](msgs, tools)

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(settings, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat_json", chat_json)
    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(llm, "chat_tools", chat_tools)
    settings.save({"yip": {"approval": "allow_all"}})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c, state


def call(name, args, i="c1"):
    return {"id": i, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


# ---------- 一句话怎么处理：模型用工具决定 ----------
async def test_decide_schedule_with_run_now(model):
    c, st = model

    async def tools(msgs, tools):
        assert {t["function"]["name"] for t in tools} >= {"reply", "start_job", "schedule", "interject"}
        return {"role": "assistant", "content": "", "tool_calls": [call("schedule", {
            "name": "邮件晨报", "rule": {"type": "weekdays", "time": "09:00"},
            "text": "整理收件箱，列出需要我处理和关注的邮件", "run_now": True})]}
    st["tools"] = tools
    r = (await c.post("/api/say", json={"text": "工作日早上九点帮我过一遍邮件，现在先来一次"})).json()
    assert r["kind"] == "schedule"
    a = (await c.get("/api/automations")).json()[0]
    assert a["when"] == "工作日 09:00" and a["last_job"], "要求现在先跑一次"


async def test_decide_start_job_rewrites_goal(model, monkeypatch):
    c, st = model
    monkeypatch.setattr(graph, "rule_plan", lambda g: [{"title": "写", "goal": g, "steps": 3}])

    async def tools(msgs, tools):
        if any(t["function"]["name"] == "start_job" for t in tools):
            return {"role": "assistant", "content": "", "tool_calls": [
                call("start_job", {"goal": "把上面聊的三家竞品（A、B、C）的定价整理成表格"})]}
        return {"role": "assistant", "content": "好了"}
    st["tools"] = tools
    r = (await c.post("/api/say", json={"text": "就按刚才说的整理一下"})).json()
    assert r["kind"] == "new"
    assert db.task(r["job"])["goal"].startswith("把上面聊的三家竞品")
    shown = db.q1("SELECT content FROM messages WHERE role='user' ORDER BY id DESC LIMIT 1")["content"]
    assert shown == "就按刚才说的整理一下", "对话里显示主人的原话"


# ---------- 验收：不过就退回返工 ----------
async def test_rework_until_machine_check_passes(model, monkeypatch):
    c, st = model
    monkeypatch.setattr(graph, "rule_plan", lambda g: [
        {"title": "开发", "goal": "写一个网页小游戏", "steps": 3, "accept": ["index.html 能打开"]}])

    async def tools(msgs, tools):
        last = msgs[-1]
        reworks = sum("验收没过" in str(m.get("content")) for m in msgs if m["role"] == "user")
        if last["role"] == "tool":
            return {"role": "assistant", "content": "做完了，入口是 index.html"}
        folder = [m for m in msgs if m["role"] == "system"][0]["content"].split("项目文件夹是「")[1].split("/」")[0]
        if reworks == 0:  # 第一次：引用了 game.js 却没写
            return {"role": "assistant", "content": "", "tool_calls": [call("write_file", {
                "path": f"{folder}/index.html", "content": "<canvas></canvas><script src='game.js'></script>"})]}
        return {"role": "assistant", "content": "", "tool_calls": [call("write_file", {
            "path": f"{folder}/game.js", "content": "const c = document.querySelector('canvas');"})]}
    st["tools"] = tools
    import uuid
    r = await c.post("/api/tasks", json={"goal": f"做一个网页小游戏 {uuid.uuid4().hex[:6]}"})  # 工作文件夹跨测试共用
    mid = r.json()["id"]
    assert await wait(lambda: db.task(mid)["status"] == "done", 40)
    k = db.children(mid)[0]
    assert k["accept"] == ["index.html 能打开"]
    assert k["verdict"]["pass"] and k["verdict"]["reworks"] == 1
    kinds = [e["type"] for e in db.q("SELECT type FROM task_events WHERE task_id=? ORDER BY id", (k["id"],))]
    assert "rework" in kinds and kinds.index("rework") < kinds.index("verified")


async def test_gives_up_after_max_rework_and_says_so(model, monkeypatch):
    c, st = model
    monkeypatch.setattr(graph, "rule_plan", lambda g: [{"title": "写", "goal": "写简报", "steps": 3, "hard": True}])  # 难活：交付前审
    st["judge"] = {"pass": False, "problems": ["没有给出处链接"], "score": 3}
    st["goal"] = {"met": False, "gaps": ["价格没有出处"]}
    n = {"calls": 0}

    async def tools(msgs, tools):
        n["calls"] += 1
        if msgs[-1]["role"] == "tool":
            return {"role": "assistant", "content": "写好了"}
        return {"role": "assistant", "content": "", "tool_calls": [call("write_file", {"path": "简报.md", "content": "# 简报\n价格 100"})]}
    st["tools"] = tools
    mid = (await c.post("/api/tasks", json={"goal": "写一份简报"})).json()["id"]
    assert await wait(lambda: db.task(mid)["status"] == "done", 40)
    k = db.children(mid)[0]
    assert not k["verdict"]["pass"] and k["verdict"]["reworks"] == verify.MAX_REWORK
    assert "还有 1 个问题" in k["note"]
    main = db.task(mid)
    assert "还差什么" in main["result"] and "出处" in main["result"]
    last = db.q1("SELECT content FROM messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (mid,))
    assert last["content"].startswith("做完了，但还差")


# ---------- 机器检查 ----------
async def test_machine_checks(tmp_path):
    (tmp_path / "a.html").write_text("<script src='x.js'></script><img src='https://e.com/a.png'>", "utf-8")
    (tmp_path / "b.md").write_text("", "utf-8")
    (tmp_path / "c.md").write_text("<｜｜DSML｜｜invoke name=\"x\">", "utf-8")
    probs = await verify.machine([tmp_path / "a.html", tmp_path / "b.md", tmp_path / "c.md", tmp_path / "d.md"], "见 d.md")
    text = "\n".join(probs)
    assert "x.js 不存在" in text and "e.com" not in text
    assert "b.md 是空文件" in text and "c.md 里混着" in text and "d.md 说写了但文件不存在" in text


@pytest.mark.skipif(not verify.browser(), reason="没有 Chrome / Edge")
async def test_browser_catches_js_error(tmp_path):
    (tmp_path / "g.html").write_text("<div>hi</div><script>notDefined();</script>", "utf-8")
    probs = await verify.check_html(tmp_path / "g.html")
    assert any("notDefined" in p for p in probs)
    (tmp_path / "ok.html").write_text("<div>你好</div><script>1+1</script>", "utf-8")
    assert await verify.check_html(tmp_path / "ok.html") == []


def test_recursion_limit_covers_reworks():
    from server import runner
    rounds = 50  # 设置里允许的最大值
    assert runner.cfg("x")["recursion_limit"] >= 4 * (rounds + 8 * verify.MAX_REWORK) + 20


async def test_missing_file_only_counts_if_still_claimed(tmp_path):
    gone = tmp_path / "tmp_test.js"
    assert await verify.machine([gone], "测完了，临时脚本已删") == []
    assert await verify.machine([gone], "测试脚本在 tmp_test.js") == ["tmp_test.js 说写了但文件不存在"]


@pytest.mark.skipif(not verify.browser(), reason="没有 Chrome / Edge")
async def test_check_page_runs_script(tmp_path):
    from server.tools import Ctx
    from server.tools.page import check_page
    (tmp_path / "g.html").write_text(
        "<body><b id=s>0</b><script>document.addEventListener('keydown',e=>{if(e.key==='ArrowUp')s.textContent='1'})</script></body>",
        "utf-8")
    out = await check_page(Ctx("t", "m", "w", tmp_path), "g.html",
                           "document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp'})); return s.textContent")
    assert "报错：没有" in out and '脚本结果："1"' in out
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".yip-check")], "副本要删掉"


async def test_watchdog_restarts_stuck_planning(fresh_db, monkeypatch):
    from server import runner
    r = runner.Runner.__new__(runner.Runner)
    r.sups = {}
    started = []
    r.start_main = lambda mid: started.append(mid)
    db.insert("tasks", id="m", parent_id=None, label="", title="t", goal="g", status="running", note="正在拆解",
              created_at=db.now() - 600_000, updated_at=db.now() - 600_000)
    task = asyncio.create_task(r.watchdog(stuck_ms=1000, every=0.05))
    await wait(lambda: started == ["m"], 3)
    db.update("tasks", "m", updated_at=db.now() - 600_000)
    await wait(lambda: len(started) == 2, 3)
    db.update("tasks", "m", updated_at=db.now() - 600_000)
    await wait(lambda: db.task("m")["status"] == "failed", 3)
    task.cancel()
    assert started == ["m", "m"] and "卡住" in db.task("m")["note"]


def test_deleted_scratch_file_is_not_a_gap(tmp_path):
    from server import verify
    gone = tmp_path / "_test3d.html"
    real = tmp_path / "game.html"
    reply = "用 _test3d.html 跑了一遍测试，已删除；入口是 game.html"
    probs = asyncio.run(verify.machine([gone, real], reply))
    assert not any("_test3d" in p for p in probs)
    assert any("game.html 说写了但文件不存在" in p for p in probs)


async def test_test_fixtures_are_not_problems(tmp_path):
    """测试故意造的空样例、用完删掉的测试数据不算问题（上次词频就是被这两条误判退回了两次）。"""
    empty = tmp_path / "样例_空文件.txt"
    empty.write_text("")
    gone = tmp_path / "测试.txt"
    assert await verify.machine([empty, gone], "用 测试.txt 和 样例_空文件.txt 测过了") == []
    real = tmp_path / "说明.md"
    real.write_text("")
    assert await verify.machine([real], "") == ["说明.md 是空文件"]



async def test_easy_job_delivers_first_then_reviews(model, monkeypatch):
    """一般的活：机器检查过了先交；后台细看发现问题，说一声、退回改，改好再交一版。"""
    c, st = model
    monkeypatch.setattr(graph, "rule_plan", lambda g: [{"title": "写", "goal": "写简报", "steps": 3}])
    judged = []

    async def judge(msgs, **k):
        text = msgs[-1]["content"]
        if "你是验收员" in text:
            judged.append(1)
            return {"pass": len(judged) > 1, "problems": [] if len(judged) > 1 else ["价格没有出处"], "score": 6}
        if "最后一道检查" in text:
            return {"met": True, "gaps": []}
        raise llm.LLMError("其余不调模型")
    monkeypatch.setattr(llm, "chat_json", judge)

    async def tools(msgs, tools):
        if msgs[-1]["role"] == "tool":
            return {"role": "assistant", "content": "写好了"}
        return {"role": "assistant", "content": "", "tool_calls": [call("write_file", {"path": "简报.md", "content": "# 简报\n价格 100"})]}
    st["tools"] = tools
    mid = (await c.post("/api/tasks", json={"goal": "写一份简报"})).json()["id"]
    says = lambda: [m["content"] for m in db.q("SELECT content FROM messages WHERE task_id=? AND role='assistant' ORDER BY id", (mid,))]  # noqa: E731
    assert await wait(lambda: any("细看下来有 1 处问题" in x for x in says()), 30), says()
    first = says()
    i = next(i for i, x in enumerate(first) if "细看下来" in x)
    assert first[i - 1].startswith("做完了"), "先交给主人，才在后台细看"
    assert await wait(lambda: says()[-1].startswith("改好了"), 30), says()
    assert db.task(mid)["status"] == "done" and len(judged) == 2, "交付前没审，后台审一次，改好后再审一次"


def test_word_format_not_fooled_by_wordcount():
    assert verify.wanted_formats("写出 wordcount.py，统计 word count") == []
    assert verify.wanted_formats("给我一份word文件") == [(".docx", "Word")]
