"""交互体验回归测试：自然语言叫停任务、待确认弹窗在对话/结束后自动消解。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import db, main
from server.router import front
from tests.conftest import seed_context


def test_natural_language_stop_detection():
    for phrase in ("不用了！", "不用了，歇息", "这个任务不用做了", "停下", "别做了", "取消这个任务"):
        assert front.is_stop(phrase) is True, phrase
    for non_stop in ("不用了，改成写周报", "不用先跟我对大纲吗？", "换个方向重新来", "帮我查一下价格"):
        assert front.is_stop(non_stop) is False, non_stop


def test_say_stops_running_task_without_overturn(fresh_db):
    with TestClient(main.app) as c:
        ctx = seed_context()
        mid = ctx["main"]
        res = c.post("/api/say", json={"text": "这个任务不用做了"})
        assert res.status_code == 200
        body = res.json()
        assert body["kind"] == "stop"
        assert body["job"] == mid
        assert db.task(mid)["status"] == "cancelled"
        assert all(k["status"] == "cancelled" for k in db.children(mid))


def test_pending_confirm_cleared_on_text_reply_and_cancel(fresh_db):
    with TestClient(main.app) as c:
        ctx = seed_context(progress=(0.85, 0.80, 0.78))
        mid = ctx["main"]
        did = db.insert(
            "decisions",
            task_id=mid,
            utterance="算了换一个",
            intent="overturn",
            target=mid,
            reason="测试待确认",
            confidence=0.9,
            need_confirm=1,
            progress=0.81,
            status="pending_confirm",
            created_at=db.now(),
        )

        # 用户文字回复「那你继续吧」：待确认弹窗自动消解，不再残留
        r = c.post("/api/say", json={"text": "那你继续吧"})
        assert r.status_code == 200
        assert r.json()["kind"] == "keep"
        assert db.q1("SELECT status FROM decisions WHERE id=?", (did,))["status"] == "cancelled"

        # 再次插入一条待确认后，用户文字叫停任务：任务取消且待确认同时作废
        did2 = db.insert(
            "decisions",
            task_id=mid,
            utterance="推翻",
            intent="overturn",
            target=mid,
            reason="测试",
            confidence=0.9,
            need_confirm=1,
            progress=0.81,
            status="pending_confirm",
            created_at=db.now(),
        )
        r2 = c.post("/api/say", json={"text": "不用了！"})
        assert r2.status_code == 200
        assert r2.json()["kind"] == "stop"
        assert db.task(mid)["status"] == "cancelled"
        assert db.q1("SELECT status FROM decisions WHERE id=?", (did2,))["status"] == "cancelled"
