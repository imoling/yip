"""定时自动化：一句话解析、下一次时间、到点触发（错过只补一次）、只一次的跑完自动关。"""
import datetime as dt

import httpx
import pytest

from server import automation as a
from server import db
from server.main import app


@pytest.mark.parametrize("text,when,body", [
    ("每个工作日早上8点整理昨天的邮件发我摘要", "工作日 08:00", "整理昨天的邮件发我摘要"),
    ("每周一、三下午3点检查招标网", "每周一、三 15:00", "检查招标网"),
    ("每月15号上午10点汇总报销", "每月 15 号 10:00", "汇总报销"),
    ("每隔30分钟看看有没有新邮件", "每隔 30 分钟", "看看有没有新邮件"),
])
def test_rule_parse(text, when, body):
    r = a.rule_parse(text)
    assert a.describe(a.validate(r["rule"])) == when and r["text"] == body


def test_next_time():
    fri = dt.datetime(2026, 11, 20, 18, 0)  # 周五晚上
    assert a.next_time({"type": "weekdays", "time": "08:00"}, fri) == dt.datetime(2026, 11, 23, 8, 0)
    assert a.next_time({"type": "monthly", "time": "09:00", "day": 31}, dt.datetime(2026, 11, 1)) == dt.datetime(2026, 12, 31, 9, 0)
    assert a.next_time({"type": "once", "at": "2026-11-01 08:00"}, fri) is None
    with pytest.raises(ValueError):
        a.validate({"type": "weekly", "time": "08:00", "days": []})


async def test_fire_and_tick(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = (await c.post("/api/automations", json={"name": "周报", "rule": {"type": "daily", "time": "17:00"},
                                                       "text": "整理一份今天的工作小结"})).json()
            assert r["when"] == "每天 17:00" and r["next_at"] > db.now()
            started = []
            start = lambda text, shown: (started.append(shown), {"id": "t_x"})[1]  # noqa: E731
            db.update("automations", r["id"], next_at=db.now() - 3 * 86400_000)  # 睡过头错过三天
            assert await a.tick(start) == 1, "错过多次只补一次"
            assert await a.tick(start) == 0
            assert started[0].startswith("⏰ 自动化「周报」")
            row = a.get(r["id"])
            assert row["next_at"] > db.now() and row["last_job"] == "t_x"
            once = (await c.post("/api/automations", json={"rule": {"type": "once", "at": "2099-01-01 08:00"}, "text": "提醒我续费"})).json()
            db.update("automations", once["id"], next_at=db.now() - 1000)
            await a.tick(start)
            assert a.get(once["id"])["enabled"] == 0, "只一次的跑完就关"
            job = (await c.post(f"/api/automations/{r['id']}/run")).json()["job"]
            assert db.task(job)["kind"] == "auto"
            msg = db.q("SELECT * FROM messages WHERE task_id=?", (job,))[0]
            assert msg["content"].startswith("⏰ 自动化「周报」") and db.task(job)["goal"] == "整理一份今天的工作小结"
            await c.post(f"/api/tasks/{job}/cancel")
            assert (await c.put(f"/api/automations/{r['id']}", json={"enabled": False})).json()["next_at"] is None
