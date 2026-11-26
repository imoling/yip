"""管理能力：队员编辑 / 复制、技能读写、连接器预置。"""
import asyncio
import time

import httpx
import pytest

from server import db

from server.main import app
from server.tools import mcp_presets


@pytest.fixture
async def c(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as cl:
            yield cl


async def test_member_copy_edit(c):
    d = (await c.get("/api/workers/intel-researcher")).json()
    assert d["form"]["global_skills"] == ["research-report"] and d["skill_docs"][0]["doc"].startswith("---")
    r = (await c.put("/api/workers/intel-researcher", json=d["form"])).json()
    assert not r["report"]["ok"], "内置队员不能直接改"
    cp = (await c.post("/api/workers/intel-researcher/copy")).json()
    assert cp["report"]["ok"] and cp["worker"]["name"].startswith("情报调研员")
    wid = cp["worker"]["id"]
    form = (await c.get(f"/api/workers/{wid}")).json()["form"]
    assert form["global_skills"] == ["research-report"], "复制带上勾的技能"
    form["introduction"] = "只查央国企的公开招标信息"
    form["global_skills"] = ["research-report", "data-table"]
    up = (await c.put(f"/api/workers/{wid}", json=form)).json()
    assert up["report"]["ok"] and up["worker"]["introduction"] == "只查央国企的公开招标信息"
    assert [k["id"] for k in up["worker"]["skills"]] == ["research-report", "data-table"], "包里的「本职」不算技能"
    await c.put(f"/api/workers/{wid}/prefs", json={"avatar": "teacher"})
    assert (await c.get(f"/api/workers/{wid}")).json()["avatar"] == "teacher"


async def test_skill_doc_roundtrip(c):
    s = (await c.post("/api/skills", json={"name": "周报整理", "description": "每周五把要点整理成周报"})).json()
    doc = (await c.get("/api/skills/doc", params={"source": "global", "id": s["id"]})).json()
    assert doc["editable"] and "周报整理" in doc["doc"]
    new = doc["doc"].replace("每周五把要点整理成周报", "每周五 17 点前整理成周报")
    assert (await c.put(f"/api/skills/{s['id']}", json={"doc": new})).status_code == 200
    bad = await c.put(f"/api/skills/{s['id']}", json={"doc": "没有 frontmatter"})
    assert bad.status_code == 400
    own = (await c.get("/api/skills/doc", params={"source": "builtin", "id": "web-game"})).json()
    assert not own["editable"] and "index.html" in own["doc"]
    assert (await c.delete("/api/skills/web-game")).status_code == 400, "内置技能删不掉"
    cp = (await c.post("/api/skills/web-game/copy")).json()
    assert (await c.get("/api/skills/doc", params={"source": "global", "id": cp["id"]})).json()["editable"]


async def test_mcp_presets(c):
    ps = (await c.get("/api/mcp/presets")).json()
    assert {"filesystem", "amap", "github", "lark"} <= {p["id"] for p in ps}
    bad = await c.post("/api/mcp/presets/amap", json={"values": {}})
    assert bad.status_code == 400 and "Key" in bad.json()["detail"]
    ok = (await c.post("/api/mcp/presets/amap", json={"values": {"key": "k123"}})).json()
    assert ok["ok"]
    lst = (await c.get("/api/mcp")).json()
    assert lst[0]["config"]["url"].endswith("key=k123") and lst[0]["requires_approval"] is False
    assert any(p["added"] for p in (await c.get("/api/mcp/presets")).json())
    assert mcp_presets.build("filesystem", {"path": "~/x"})["args"][-1].endswith("/x")


async def test_create_with_avatar_globals_and_try(c):
    g = (await c.post("/api/skills", json={"name": "周报整理", "description": "整理周报"})).json()
    form = {"name": "周报助理", "introduction": "每周五整理周报", "avatar": "teacher", "global_skills": [g["id"]],
            "skills": [], "sample": "把这周的要点整理成周报"}
    r = (await c.post("/api/workers", json=form)).json()
    assert r["report"]["ok"], r
    w = r["worker"]
    assert w["avatar"] == "teacher" and w["global_skills"] == [g["id"]] and len(w["skills"]) == 1, "只算勾的技能，包里那条「本职」不显示"
    d = (await c.get(f"/api/workers/{w['id']}")).json()
    assert d["form"]["sample"] == "把这周的要点整理成周报"
    t = (await c.post(f"/api/workers/{w['id']}/try", json={})).json()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15 and db.task(t["job"])["status"] not in ("done", "failed"):
        await asyncio.sleep(0.1)
    job = (await c.get(f"/api/tasks/{t['job']}")).json()
    assert job["status"] == "done" and job["kind"] == "trial" and job["children"][0]["worker_id"] == w["id"]
    st = (await c.get("/api/state")).json()
    assert all(j["id"] != t["job"] for j in st["jobs"]), "试用不进对话和看板"
    assert not any(m["task_id"] == t["job"] for m in st["messages"])
    assert (await c.delete(f"/api/trials/{t['job']}")).json()["ok"] and db.task(t["job"]) is None
