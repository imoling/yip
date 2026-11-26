"""记忆：去重、相关度、「记住…」直接记、聊天和拆活会带上。"""
import asyncio
import time

import httpx

from server import db, memory
from server.main import app
from server.router import front


def test_add_dedup_and_relevant(fresh_db):
    a = memory.add("preference", "给领导的材料先结论后论据")
    b = memory.add("preference", "给领导的材料先结论后论据。")
    assert a and b and a["id"] == b["id"], "一样的话不重复记"
    memory.add("lesson", "DeepSeek 官网定价页直连打不开，用第三方页面交叉核对")
    memory.add("lesson", "合同审查要先看违约条款")
    memory.add("fact", "主人在中科大读 MBA")
    rel = memory.relevant("查一下 DeepSeek 的 API 定价")
    kinds = [m["kind"] for m in rel]
    assert "preference" in kinds and "fact" in kinds, "偏好和关于你总是带上"
    assert any("DeepSeek" in m["content"] for m in rel)
    assert not any("违约" in m["content"] for m in rel), "不相关的经验不带"
    assert "你记得的事" in memory.block("DeepSeek")
    assert memory.add("nope", "x") is None and memory.add("preference", "a") is None


def test_explicit_parse():
    assert memory.explicit("记住：我下周二讲课") == "我下周二讲课"
    assert memory.explicit("帮我记一下 周报每周五交") == "周报每周五交"
    assert memory.explicit("今天天气不错") is None
    assert memory.guess_kind("我在杭州") == "fact"
    assert memory.guess_kind("以后表格都用 Markdown") == "preference"


async def test_say_remember_and_team(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/say", json={"text": "记住：以后表格都用 Markdown"})
            assert r.json()["kind"] == "chat"
            t0 = time.monotonic()
            while time.monotonic() - t0 < 5 and not db.q("SELECT * FROM memories"):
                await asyncio.sleep(0.05)
            rows = (await c.get("/api/memory")).json()
            assert rows and rows[0]["content"] == "以后表格都用 Markdown" and rows[0]["source"] == "manual"
            msg = db.q("SELECT * FROM messages WHERE role='assistant'")[-1]
            assert msg["meta"]["kind"] == "memory" and "记住了" in msg["content"]
            mid = rows[0]["id"]
            await c.put(f"/api/memory/{mid}", json={"content": "表格一律 Markdown"})
            assert (await c.get("/api/memory")).json()[0]["content"] == "表格一律 Markdown"
            await c.delete(f"/api/memory/{mid}")
            assert not (await c.get("/api/memory")).json()
    assert "情报调研员" in front._team()


async def test_rename_is_remembered(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/say", json={"text": "你叫茉莉"})
            t0 = time.monotonic()
            while time.monotonic() - t0 < 5 and not db.q("SELECT * FROM memories WHERE kind='persona'"):
                await asyncio.sleep(0.05)
            row = db.q1("SELECT * FROM memories WHERE kind='persona'")
            assert row and "茉莉" in row["content"]
            assert memory.assistant_name() == "茉莉"
            assert (await c.get("/api/state")).json()["name"] == "茉莉"
            assert any("关于 Yip" in m for m in [memory.block("随便")])


def test_persona_kind():
    assert memory.guess_kind("你说话简短一点") == "persona"
    assert memory.quick("今天天气") is None
