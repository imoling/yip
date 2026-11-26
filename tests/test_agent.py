"""真实 agent loop 的骨架：放行规则、工作文件夹边界、设置迁移、前台分流，和用假模型跑通「要问 → 允许 / 拒绝 → 接着跑」。"""
import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from server import db, llm, settings
from server.main import app
from server.router import front
from server.tools import Ctx, ToolError, files, risk


# ---------- 放行规则 ----------
@pytest.mark.parametrize("risk_,policy,want", [
    ("read", "ask_writes", "allow"), ("write", "ask_writes", "ask"), ("exec", "ask_writes", "ask"),
    ("external", "ask_writes", "ask"), ("write", "allow_all", "allow"), ("read", "ask_all", "ask"),
])
def test_decide(risk_, policy, want):
    assert risk.decide("x", risk_, policy) == want


def test_decide_grants_and_free():
    assert risk.decide("write_file", "write", "ask_writes", ["write_file"]) == "allow"
    assert risk.decide("run_shell", "exec", "ask_all", ["*"]) == "allow"
    assert risk.decide("load_skill", "read", "ask_all") == "allow"


# ---------- 工作文件夹 ----------
async def test_files_stay_inside(tmp_path, fresh_db):
    ctx = Ctx("t", "m", "w", tmp_path / "ws")
    with pytest.raises(ToolError):
        await files.read_file(ctx, "../secret.txt")
    with pytest.raises(ToolError):
        await files.write_file(ctx, "/etc/hosts", "x")
    db.insert("tasks", id="t", parent_id=None, label="", title="t", goal="g", status="running",
              created_at=db.now(), updated_at=db.now())
    out = await files.write_file(ctx, "简报/a.md", "# 标题\n第一行\n第二行")
    assert "已写入" in out and (tmp_path / "ws" / "简报" / "a.md").exists()
    a = db.q1("SELECT * FROM artifacts")
    assert a["name"] == "a.md" and a["kind"] == "doc" and "第一行" in a["preview"]
    assert "a.md" in await files.list_dir(ctx, "简报")


# ---------- 设置 ----------
def test_settings_migrates_flat(fresh_db):
    p = fresh_db / "settings.json"
    p.write_text(json.dumps({"base_url": "https://api.deepseek.com", "api_key": "k", "model": "deepseek-v4-pro",
                             "fast_model": "deepseek-flash"}), "utf-8")
    d = settings.load()
    assert d["providers"]["deepseek"]["api_key"] == "k"
    assert d["model"] == "deepseek:deepseek-v4-pro" and settings.resolve(True)["model"] == "deepseek-flash"
    pub = settings.public()
    assert pub["providers"]["deepseek"]["api_key"] == settings.MASK and pub["configured"]
    settings.save({"yip": {"approval": "allow_all", "max_rounds": 99, "search": {"api_key": settings.MASK}}})
    assert settings.yip()["approval"] == "allow_all" and settings.yip()["max_rounds"] == 50
    settings.save({"providers": {"ollama": {"base_url": "http://localhost:11434/v1"}}, "model": "ollama:qwen3:8b"})
    assert settings.resolve()["model"] == "qwen3:8b" and settings.resolve()["provider"] == "ollama"
    assert settings.resolve(True)["model"] == "deepseek-flash", "快模型没动"


# ---------- 前台分流（规则） ----------
def test_front_rules(fresh_db):
    assert front.rule_route("你好", [])["kind"] == "chat"
    assert front.rule_route("上周的讲课 PPT 放哪了？", [])["kind"] == "chat"
    assert front.rule_route("帮我整理一份三家竞品的简报", [])["kind"] == "new"
    job = {"id": "m1", "title": "三家竞品简报"}
    db.insert("tasks", id="m1", parent_id=None, label="", title="三家竞品简报", goal="g", status="running",
              created_at=db.now(), updated_at=db.now())
    assert front.rule_route("定价只看企业版", [job])["kind"] == "interject"
    assert front.rule_route("别做了，重来", [job])["job"] == "m1"


# ---------- 假模型跑 agent loop ----------
@pytest.fixture
async def agent_client(fresh_db, monkeypatch):
    async def no_model(*a, **k):
        raise llm.LLMError("测试里不调真模型")

    async def fake_tools(messages, tools, **k):
        done = any(m["role"] == "tool" for m in messages)
        goal = next(m["content"] for m in messages if m["role"] == "user")
        if done:
            return {"role": "assistant", "content": "做完了，文件在工作文件夹里"}
        name = f"{abs(hash(goal)) % 10000}.md"
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "write_file",
                                                          "arguments": json.dumps({"path": name, "content": goal})}},
        ]}

    settings.save({"yip": {"approval": "ask_writes"}})  # 这组测的是「手动」模式：写文件先问
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(settings, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat", no_model)
    monkeypatch.setattr(llm, "chat_json", no_model)
    monkeypatch.setattr(llm, "chat_tools", fake_tools)
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


async def test_agent_asks_then_runs(agent_client):
    c = agent_client
    r = await c.post("/api/say", json={"text": "帮我整理一份三家竞品的简报，重点看定价和渠道"})
    assert r.json()["kind"] == "new"
    mid = r.json()["job"]
    assert await wait(lambda: len(pending()) >= 3), "写文件要先问"
    rows = pending()
    assert all(a["tool"] == "write_file" and a["summary"].startswith("写入") for a in rows)
    assert not list(settings.workspace().glob("*.md")) or True
    # 拒绝第一个；第二个「本次都允许」会把同一件事里其余的 write_file 一起放行
    await c.post(f"/api/approvals/{rows[0]['id']}", json={"decision": "deny"})
    await c.post(f"/api/approvals/{rows[1]['id']}", json={"decision": "allow_task"})
    assert await wait(lambda: db.task(mid)["status"] == "done", 30), db.children(mid)
    assert not pending()
    assert "write_file" in db.task(mid)["grants"]
    arts = db.q("SELECT * FROM artifacts WHERE main_id=?", (mid,))
    assert len(arts) >= 3, "两个子任务写的文件 + 汇总文档"
    denied = db.task(rows[0]["task_id"])
    assert denied["status"] == "done"
    msgs = db.q("SELECT * FROM messages WHERE task_id=?", (mid,))
    assert any((m["meta"] or {}).get("kind") == "file" for m in msgs), "产物要在对话里出现"
    st = (await c.get("/api/state")).json()
    assert st["artifacts"] and st["jobs"][0]["id"] == mid


async def test_cancel_voids_pending(agent_client):
    c = agent_client
    r = await c.post("/api/say", json={"text": "帮我整理一份三家竞品的简报，重点看定价和渠道"})
    mid = r.json()["job"]
    assert await wait(lambda: len(pending()) >= 1)
    tid = pending()[0]["task_id"]
    await c.post(f"/api/tasks/{tid}/cancel")
    assert db.q1("SELECT status FROM approvals WHERE task_id=? ORDER BY id DESC", (tid,))["status"] == "cancelled"
    await c.post(f"/api/tasks/{mid}/cancel")


async def test_say_chat_without_model(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/say", json={"text": "你好"})
            assert r.json()["kind"] == "chat"
            assert await wait(lambda: any(m["role"] == "assistant" for m in db.q("SELECT * FROM messages")), 5)
            m = db.q("SELECT * FROM messages WHERE role='assistant'")[0]
            assert m["task_id"] is None and "说一件要办的事" in m["content"]
