"""三个动作的行为：在进程内起整个应用（含 LangGraph），走真实接口。"""
import asyncio
import time
from pathlib import Path

import httpx
import pytest

from server import db, runner
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


async def start(client):
    r = await client.post("/api/tasks", json={"goal": "帮我整理一份三家竞品的简报，重点看定价和渠道。"})
    mid = r.json()["id"]
    assert await wait(lambda: sum(k["status"] == "running" for k in db.children(mid)) >= 2, 5)
    kids = {k["label"]: k for k in db.children(mid)}
    return mid, kids


async def say(client, mid, text):
    r = await client.post("/api/interject", json={"text": text, "task_id": mid})
    assert r.status_code == 200
    return r.json()


async def test_full_flow_augment_append_and_summary(client):
    mid, kids = await start(client)
    assert all(k["worker_id"] and k["assign_reason"] for k in kids.values())
    before = {l: list(k["constraints"]) for l, k in kids.items()}
    d = await say(client, mid, "定价那块重点看企业版，别管个人版")
    assert d["intent"] == "augment" and d["target"] == kids["A"]["id"] and d["action"] == "steering"
    await wait(lambda: db.task(kids["A"]["id"])["constraints"])
    assert db.task(kids["A"]["id"])["constraints"] == ["定价那块重点看企业版，别管个人版"]
    assert db.task(kids["B"]["id"])["constraints"] == before["B"]
    assert db.task(kids["C"]["id"])["constraints"] == before["C"]
    run_a = db.task(kids["A"]["id"])["run_id"]

    d = await say(client, mid, "顺便再加一家竞品")
    assert d["intent"] == "append" and d["action"] == "enqueue"
    await wait(lambda: len(db.children(mid)) == 4)
    new = [k for k in db.children(mid) if k["label"] == "D"][0]
    assert new["status"] == "waiting"
    assert db.task(kids["A"]["id"])["run_id"] == run_a, "追加不应打断 A"

    assert await wait(lambda: db.task(mid)["status"] == "done", 40)
    a = db.task(kids["A"]["id"])
    assert "企业版" in a["result"] and "个人版" not in a["result"].split("已按约束")[0]
    assert db.task(new["id"])["status"] == "done"
    assert Path(db.task(mid)["artifact"]).exists()
    assert await wait(lambda: db.q("SELECT * FROM memories WHERE task_id=?", (mid,)), 5), "交付后才沉淀记忆，等一下"
    assert 1 <= len(db.q("SELECT * FROM memories WHERE task_id=?", (mid,))) <= 3


async def test_overturn_needs_confirm_above_70(client):
    mid, kids = await start(client)
    a = kids["A"]["id"]
    await runner.get().cancel(a)                    # 冻住 A，模拟跑到 76%
    db.update("tasks", a, progress=0.76, status="running")
    d = await say(client, mid, "别做定价了，改看服务政策")
    assert d["intent"] == "overturn" and d["need_confirm"] and d["cost"] == "high" and d["action"] == "await_confirm"
    await asyncio.sleep(0.3)
    assert db.task(a)["progress"] == 0.76, "不点确认不动"
    r = await client.post(f"/api/decisions/{d['decision_id']}/confirm")
    assert r.status_code == 200
    ev = db.q("SELECT * FROM task_events WHERE task_id=? AND type='discarded'", (a,))
    assert ev and ev[0]["payload"]["progress"] == 0.76
    t = db.task(a)
    assert t["title"] == "服务政策" and t["progress"] < 0.5 and t["label"] == "A"
    assert len([k for k in db.children(mid) if k["label"] == "A"]) == 1, "推翻后 task_id 不变，只有一条 A"


async def test_overturn_mid_progress_runs_directly(client):
    mid, kids = await start(client)
    b = kids["B"]["id"]
    d = await say(client, mid, "渠道不查了，换成查客户案例")
    assert d["intent"] == "overturn" and not d["need_confirm"] and d["target"] == b
    assert await wait(lambda: db.q("SELECT 1 FROM task_events WHERE task_id=? AND type='discarded'", (b,)))
    assert db.task(b)["title"] == "客户案例"


async def test_override_changes_behavior(client):
    mid, kids = await start(client)
    a = kids["A"]["id"]
    d = await say(client, mid, "定价只看企业版")
    assert d["intent"] == "augment"
    await wait(lambda: db.q1("SELECT status FROM decisions WHERE id=?", (d["decision_id"],))["status"] == "done")
    run_before = db.task(a)["run_id"]
    r = await client.post(f"/api/decisions/{d['decision_id']}/override", json={"intent": "overturn"})
    assert r.json()["action"] == "interrupt"
    row = db.q1("SELECT * FROM decisions WHERE id=?", (d["decision_id"],))
    assert row["overridden"] == 1 and row["final_action"] == "overturn"
    assert db.task(a)["run_id"] != run_before
    assert "定价只看企业版" not in (db.task(a)["constraints"] or []), "改判断要撤掉原来加的约束"
    s = (await client.get("/api/stats")).json()
    assert s["overridden"] == 1


async def test_append_overridden_to_augment_removes_queued(client):
    mid, kids = await start(client)
    d = await say(client, mid, "顺便再加一家竞品")
    await wait(lambda: db.q1("SELECT status FROM decisions WHERE id=?", (d["decision_id"],))["status"] == "done")
    assert len(db.children(mid)) == 4
    await client.post(f"/api/decisions/{d['decision_id']}/override", json={"intent": "augment"})
    assert len(db.children(mid)) == 3


async def test_reassign(client):
    mid, kids = await start(client)
    c = kids["C"]["id"]
    r = await client.post(f"/api/tasks/{c}/assign", json={"worker_id": "general-assistant"})
    assert r.json()["worker_id"] == "general-assistant"
    assert db.q("SELECT 1 FROM task_events WHERE task_id=? AND type='reassigned'", (c,))


async def test_refresh_keeps_receipts(client):
    mid, _ = await start(client)
    await say(client, mid, "定价只看企业版")
    d = (await client.get(f"/api/tasks/{mid}")).json()
    assert d["decisions"] and any(m["role"] == "decision" for m in d["messages"])
