"""小队：队友状态进提示词、留言送达、测试退回给开发返工、改完自动复查，下游等复查完才开工。"""
import json

from server import db, graph, team
from tests.test_verify import call, model, wait  # noqa: F401 — 复用假模型夹具


async def test_send_back_rework_and_recheck(model, monkeypatch):
    c, st = model
    monkeypatch.setattr(graph, "rule_plan", lambda g: [
        {"title": "开发", "goal": "GOAL-DEV 写 calc.js", "steps": 3},
        {"title": "测试", "goal": "GOAL-TEST 测 calc.js", "steps": 3, "after": [0]},
        {"title": "审查", "goal": "GOAL-REVIEW 审 calc.js", "steps": 3, "after": [1]},
    ])
    seen = {}

    async def tools(msgs, tools):
        sys = msgs[0]["content"]
        names = {t["function"]["name"] for t in tools}
        last = msgs[-1]
        users = [m["content"] for m in msgs if m["role"] == "user"]
        if "GOAL-DEV" in sys:
            if last["role"] == "tool":
                return {"role": "assistant", "content": "calc.js 写好了"}
            fixed = any("退回这些问题" in u for u in users)
            return {"role": "assistant", "content": "", "tool_calls": [call("write_file", {
                "path": "calc.js", "content": "const add=(a,b)=>a+b;" if fixed else "const add=(a,b)=>a-b;"})]}
        if "GOAL-TEST" in sys:
            seen["test_roster"] = "## 小队" in sys and "A（" in sys
            seen["test_tools"] = {"team_post", "send_back"} <= names
            if last["role"] == "tool":
                return {"role": "assistant", "content": "测完了"}
            if any("改好了" in u for u in users):
                return {"role": "assistant", "content": "", "tool_calls": [call("team_post", {"text": "复测通过"})]}
            return {"role": "assistant", "content": "", "tool_calls": [call("send_back", {
                "to": "A", "problems": ["calc.js 的 add 写成了减法"]})]}
        if "GOAL-REVIEW" in sys:
            seen["review_heard"] = any("复测通过" in u for u in users)
            return {"role": "assistant", "content": "审查完了，没问题"}
        return {"role": "assistant", "content": "好"}
    st["tools"] = tools
    mid = (await c.post("/api/tasks", json={"goal": "写个加法函数"})).json()["id"]
    assert await wait(lambda: db.task(mid)["status"] == "done", 60)
    a, b, r = sorted(db.children(mid), key=lambda k: k["label"])
    assert a["bounces"] == 1 and all(k["status"] == "done" for k in (a, b, r))
    from pathlib import Path
    f = db.q1("SELECT path FROM artifacts WHERE name='calc.js' ORDER BY id DESC LIMIT 1")["path"]
    assert "a+b" in Path(f).read_text("utf-8"), "开发按退回的问题改了"
    ev = db.q("SELECT task_id, type, created_at FROM task_events WHERE type IN ('reopened','dequeued') ORDER BY id")
    order = [(e["task_id"], e["type"]) for e in ev]
    assert (a["id"], "reopened") in order and (b["id"], "reopened") in order
    assert order.index((b["id"], "reopened")) < order.index((r["id"], "dequeued")), "审查要等复测完"
    assert seen == {"test_roster": True, "test_tools": True, "review_heard": True}
    kinds = [m["kind"] for m in team.listing(mid)]
    assert "issue" in kinds and kinds.count("note") >= 2  # 退回、改好了请复查、复测通过
    st_json = (await c.get("/api/state")).json()
    assert next(j for j in st_json["jobs"] if j["id"] == mid)["team"]


async def test_send_back_limits(model, monkeypatch):
    c, st = model
    from server.tools import Ctx, ToolError
    import pytest
    db.insert("tasks", id="m", parent_id=None, label="", title="t", goal="g", status="running",
              created_at=db.now(), updated_at=db.now())
    for i, (lab, status) in enumerate([("A", "running"), ("B", "running"), ("C", "done")]):
        db.insert("tasks", id=lab, parent_id="m", label=lab, title=lab, goal="g", status=status, worker_id="general",
                  bounces=team.MAX_BOUNCE if lab == "C" else 0, created_at=db.now() + i, updated_at=db.now())
    ctx = Ctx("B", "m", "general", db.ROOT)
    with pytest.raises(ToolError, match="还在做"):
        await team.send_back(ctx, "A", ["x"])
    with pytest.raises(ToolError, match="已经被退回"):
        await team.send_back(ctx, "C", ["x"])
    with pytest.raises(ToolError, match="没有 Z"):
        await team.team_post(ctx, "hi", "Z")
    assert "已发给A" == await team.team_post(ctx, "接口改成 add(a, b)", "A")
    text, last = team.unread("m", "A", 0)
    assert "B（" in text and "接口改成" in text and last > 0
    assert team.unread("m", "A", last)[0] == "" and team.unread("m", "B", 0)[0] == "", "自己发的不回给自己"
