"""Deep Agents 引擎（试验）：用假模型走一遍「写文件要问 → 放行 → 接着跑 → 验收 → 交付」，产物要记上。"""
import asyncio
import time

import httpx
import pytest

pytest.importorskip("deepagents")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from server import db, llm, settings, worker_deep  # noqa: E402
from server.main import app  # noqa: E402


class FakeModel(BaseChatModel):
    """第一次：写一个文件；看到写文件的结果后：交代做完了。"""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        wrote = any(isinstance(m, ToolMessage) and m.name == "write_file" for m in messages)
        if wrote:
            msg = AIMessage("做完了：写了 note.md，打开就能看")
        else:
            goal = next(m.content for m in messages if m.type == "human")
            path = str(settings.workspace() / "deep-test" / f"note-{abs(hash(goal)) % 10000}.md")
            msg = AIMessage("", tool_calls=[{"id": f"w{abs(hash(goal)) % 997}", "name": "write_file",
                                             "args": {"file_path": path, "content": f"# 笔记\n{goal}"}}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.fixture
async def deep_client(fresh_db, monkeypatch):
    async def no_model(*a, **k):
        raise llm.LLMError("测试里不调真模型")
    monkeypatch.setenv("SA_ENGINE", "deep")
    settings.save({"yip": {"approval": "ask_writes"}})  # 测「手动」模式下写文件要问
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(settings, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat", no_model)
    monkeypatch.setattr(llm, "chat_json", no_model)
    monkeypatch.setattr(worker_deep, "_model", lambda *a, **k: FakeModel())
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


def pending():
    return db.q("SELECT * FROM approvals WHERE status='pending' ORDER BY id")


async def test_deep_engine_asks_then_delivers(deep_client):
    c = deep_client
    mid = (await c.post("/api/tasks", json={"goal": "写一份读书笔记 " + str(time.time())})).json()["id"]
    assert await wait(lambda: len(pending()) >= 1), "写文件要先问"
    rows = pending()
    assert rows[0]["tool"] == "write_file" and rows[0]["summary"].startswith("写入 deep-test/note-")
    kids = db.children(mid)
    assert all(k["status"] == "running" for k in kids)
    for r in rows:
        await c.post(f"/api/approvals/{r['id']}", json={"decision": "allow_task"})
    assert await wait(lambda: db.task(mid)["status"] == "done", 30), [(k["label"], k["status"], k["note"]) for k in db.children(mid)]
    for k in db.children(mid):
        assert k["status"] == "done" and "做完了" in k["result"]
        steps = [e for e in db.q("SELECT * FROM task_events WHERE task_id=? AND type='step'", (k["id"],))]
        assert steps, "Deep Agent 的每一步要进动态"
    arts = db.q("SELECT * FROM artifacts WHERE main_id=? AND name LIKE 'note-%'", (mid,))
    assert arts and all(settings.workspace().joinpath("deep-test", a["name"]).exists() for a in arts)
    assert not pending()


async def test_deep_engine_denied_write(deep_client):
    c = deep_client
    mid = (await c.post("/api/tasks", json={"goal": "写一份会议纪要 " + str(time.time())})).json()["id"]
    assert await wait(lambda: len(pending()) >= 1)
    t0 = time.monotonic()  # 两个队员各自来问，后来的那个也要拒掉
    while db.task(mid)["status"] != "done" and time.monotonic() - t0 < 30:
        for r in pending():
            await c.post(f"/api/approvals/{r['id']}", json={"decision": "deny"})
        await asyncio.sleep(0.05)
    assert db.task(mid)["status"] == "done"
    assert not db.q("SELECT * FROM artifacts WHERE main_id=? AND name LIKE 'note-%'", (mid,)), "没同意就不该写"
    assert db.q("SELECT * FROM task_events WHERE type='denied'")


def test_engine_switch(monkeypatch, fresh_db):
    from server import runner
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.delenv("SA_ENGINE", raising=False)
    assert runner._mode() == "agent"
    settings.save({"yip": {"engine": "deep"}})
    assert runner._mode() == "deep"
    settings.save({"yip": {"engine": "bogus"}})
    assert settings.yip()["engine"] == "deep"


class RmModel(FakeModel):
    """先跑一条普通命令，再跑一条删东西的命令，最后交代。"""

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        ran = [m for m in messages if isinstance(m, ToolMessage) and m.name == "execute"]
        if len(ran) == 0:
            msg = AIMessage("", tool_calls=[{"id": "e1", "name": "execute", "args": {"command": "echo hi > ok.txt"}}])
        elif len(ran) == 1:
            msg = AIMessage("", tool_calls=[{"id": "e2", "name": "execute", "args": {"command": "rm -rf old"}}])
        else:
            msg = AIMessage("做完了")
        return ChatResult(generations=[ChatGeneration(message=msg)])


async def test_deep_auto_mode_asks_only_for_risky_command(deep_client, monkeypatch):
    settings.save({"yip": {"approval": "auto"}})
    monkeypatch.setattr(worker_deep, "_model", lambda *a, **k: RmModel())
    from server import graph
    monkeypatch.setattr(graph, "rule_plan", lambda goal: [{"title": "清理", "goal": goal, "steps": 3, "after": []}])
    mid = (await deep_client.post("/api/tasks", json={"goal": "清理一下 " + str(time.time())})).json()["id"]
    assert await wait(lambda: len(pending()) >= 1), "删东西要问"
    rows = pending()
    assert len(rows) == 1 and rows[0]["tool"] == "execute" and "rm -rf old" in rows[0]["summary"]
    steps = db.q("SELECT * FROM task_events WHERE type='step' AND json_extract(payload,'$.tool')='execute'")
    assert steps and "echo hi" in steps[0]["payload"]["text"], "普通命令不问，直接跑了"
    await deep_client.post(f"/api/approvals/{rows[0]['id']}", json={"decision": "deny"})
    assert await wait(lambda: db.task(mid)["status"] == "done", 30)
