"""清屏和聊天记录：清屏不删记录、Yip 记得前情、能按文件 / 图片 / 链接 / 日期搜，Yip 自己也能翻。"""
import datetime as dt

import httpx

from server import chatlog, db, events, setup
from server.main import app


def _say(role, text, **meta):
    return events.message(None, role, text, **meta)


async def test_clear_keeps_record_and_memory(fresh_db):
    _say("user", "帮我查一下阿森纳这赛季的战绩")
    _say("assistant", "阿森纳英超 9 胜 1 平 1 负，积 28 分，暂列第一")
    out = await chatlog.clear()  # 没配模型：前情退回主人说过的话
    assert out["summary"] and "阿森纳" in out["summary"]
    _say("user", "你好")
    assert [m["content"] for m in chatlog.recent(10)] == ["你好"], "清屏后只带新的话"
    assert "阿森纳" in chatlog.earlier(), "前情照样记得"
    assert len(db.q("SELECT * FROM messages")) == 4, "记录一条没删"
    assert (await chatlog.clear())["summary"] == "你好"
    assert (await chatlog.clear())["summary"] == "", "没说话就不落标记"


def test_search_filters(fresh_db):
    a = db.insert("artifacts", task_id="t", main_id="m", worker_id=None, path="/ws/定价对比.md", name="定价对比.md",
                  kind="doc", size=10, preview="", created_at=db.now())
    p = db.insert("artifacts", task_id="t", main_id="m", worker_id=None, path="/ws/baidu.png", name="baidu.png",
                  kind="other", size=10, preview="", created_at=db.now())
    _say("user", "看看 https://example.com/pricing 这个页面")
    _say("assistant", "做完了，详细的在报告里", kind="file", final=True, artifact_id=a, files=[p])
    _say("user", "今天天气怎么样")
    assert [r["content"] for r in chatlog.search(kind="link")] == ["看看 https://example.com/pricing 这个页面"]
    f = chatlog.search(kind="file")
    assert len(f) == 1 and f[0]["docs"][0]["name"] == "定价对比.md"
    assert chatlog.search(kind="image")[0]["images"][0]["name"] == "baidu.png"
    assert len(chatlog.search(q="定价")) == 1, "按文件名也能搜到"
    today = dt.date.today().isoformat()
    assert len(chatlog.search(date=today)) == 3 and chatlog.search(date="2001-01-01") == []
    assert chatlog.dates(today[:7]) == [today]


async def test_api_and_slash(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            _say("user", "整理一下周报")
            r = (await c.post("/api/say", json={"text": "/清屏"})).json()
            assert r["kind"] == "clear" and r["summary"]
            h = (await c.get("/api/history", params={"q": "周报"})).json()
            assert [x["content"] for x in h] == ["整理一下周报"]
            assert (await c.get("/api/history", params={"date": "11-24"})).status_code == 400


async def test_yip_can_search_history(fresh_db):
    _say("user", "上次的定价表放哪了")
    out = await setup.call("search_history", {"q": "定价"})
    assert "定价表" in out
    assert await setup.call("search_history", {"q": "不存在的词"}) == "聊天记录里没找到"
