"""右侧实时浏览器面板：状态、动作广播、停止接管、WebSocket 认证。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from server import main
from server.tools import ToolError, browser


def test_norm_url():
    assert browser._norm_url("example.com/a") == "https://example.com/a"
    assert browser._norm_url("http://127.0.0.1:8770") == "http://127.0.0.1:8770"
    assert browser._norm_url("  ") == ""


def test_action_broadcast_and_stop():
    b = browser.Browser()
    q: asyncio.Queue = asyncio.Queue(maxsize=8)
    b._subs.add(q)
    b.set_action("点一下「登录」", box={"x": 10, "y": 20, "w": 80, "h": 30}, task_id="t_1")
    msg = q.get_nowait()
    assert msg["type"] == "action" and msg["data"]["busy"] is True
    assert msg["data"]["box"]["x"] == 10

    res = b.stop_ai()
    assert res["stopped"] == "t_1"
    assert b.action is None
    with pytest.raises(ToolError, match="停下"):
        b.check_stopped("t_1")
    # 消费一次后不再重复抛错
    b.check_stopped("t_1")


def test_browser_ws_and_token_guard(monkeypatch):
    monkeypatch.setenv("SA_API_TOKEN", "secret-tok")
    client = TestClient(main.app)

    # 无 token 的 WS 连接应被关闭
    with pytest.raises(Exception):
        with client.websocket_connect("/api/browser/ws"):
            pass

    # 带正确 token 可连接并立刻收到 state 消息
    with client.websocket_connect("/api/browser/ws?token=secret-tok") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "state"
        assert "tabs" in msg["data"] and "viewport" in msg["data"]

    # /api/browser/open 缺少网址返回 400
    r = client.post("/api/browser/open", json={"url": ""}, headers={"Authorization": "Bearer secret-tok"})
    assert r.status_code == 400
