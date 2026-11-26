"""Yip 在聊天里管自己的设置：贴 key 过来她自己写进去、试一次；key 不进库、不进模型、不进记忆。搜索日常 + 深度两档。"""
import asyncio
import json
import time

import httpx
import pytest

from server import db, llm, settings, setup
from server.main import app
from server.tools import Ctx, ToolError, web

KEY = "tvly-dev-AbCdEf1234567890XyZ9"


def test_hide_and_reveal():
    t, n = setup.hide_secrets(f"好的，key：{KEY}")
    assert n == 1 and KEY not in t and "【密钥 tvly-…XyZ9】" in t
    assert setup.reveal("【密钥 tvly-…XyZ9】") == KEY
    assert setup.reveal("平常的字") == "平常的字"
    t2, n2 = setup.hide_secrets("看看 https://github.com/NousResearch/hermes-agent/issues/377 这个")
    assert n2 == 0, "链接、编号不算 key"


async def test_set_search_tests_before_saving(fresh_db, monkeypatch):
    async def fake(provider, key, q, n):
        if key != KEY:
            raise httpx.HTTPStatusError("401", request=None, response=None)
        return [{"title": "t", "url": "u", "snippet": "s"}]
    monkeypatch.setattr(web, "search_with", fake)
    setup.hide_secrets(KEY)
    assert "没通" in await setup.set_search("tavily", "tvly-wrong-0000000000")
    assert settings.yip()["search"]["provider"] == "keenable", "默认是 Keenable"
    out = await setup.set_search("tavily", "【密钥 tvly-…XyZ9】")
    assert "已保存" in out and KEY not in out
    assert settings.yip()["search"] == {**settings.yip()["search"], "provider": "tavily", "api_key": KEY}
    assert settings.public()["yip"]["search"]["api_key"] == settings.MASK


async def test_connect_tavily_in_chat_tools(fresh_db, monkeypatch):
    """接 Tavily 连接器：先试连，通了才存进 mcp.json；深度搜索就用它的 key；能停用、删掉。"""
    from server.tools import mcp
    tried = []

    async def fake_test(name, cfg):
        tried.append(cfg["url"])
        return {"ok": KEY in cfg["url"], "tools": [{"name": "tavily_search"}], "error": "没有权限，检查密钥或令牌"}
    monkeypatch.setattr(mcp, "test", fake_test)
    setup.hide_secrets(KEY)
    assert "没通" in await setup.add_connector("tavily", {"key": "tvly-wrong-0000000000"})
    assert not mcp.load()
    out = await setup.add_connector("tavily", {"api_key": "【密钥 tvly-…XyZ9】"})  # 字段名写错了也认
    assert "接好了" in out and KEY not in out
    (name, cfg), = mcp.load().items()
    assert cfg["preset"] == "tavily" and KEY in cfg["url"] and cfg["requires_approval"] is False
    assert web.deep_search() == ("tavily", KEY)
    assert "深度搜索：Tavily（连接器已接）" in setup.get_setup()
    assert "已接（Tavily 搜索）" in setup.list_connectors()
    assert "停用" in await setup.set_connector(name, False)
    assert web.deep_search() == ("", "")
    assert "删掉" in await setup.remove_connector(name)
    assert not mcp.load()


async def test_search_falls_back_to_deep(fresh_db, monkeypatch):
    from server.tools import mcp
    mcp.save({"Tavily 搜索": {"url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={KEY}", "preset": "tavily"}})
    used = []

    async def fake(provider, key, q, n):
        used.append(provider)
        if provider in ("keenable", "ddgs"):
            raise RuntimeError("限流")
        return [{"title": "阿森纳", "url": "https://x", "snippet": "9 胜"}]
    monkeypatch.setattr(web, "search_with", fake)
    ctx = Ctx("t", "m", "w", settings.workspace())
    assert "阿森纳" in await web.web_search(ctx, "阿森纳战绩")
    assert used == ["keenable", "ddgs", "tavily"], "Keenable 不行换 DuckDuckGo，再不行换深度"
    used.clear()
    await web.web_search(ctx, "阿森纳战绩", deep=True)
    assert used == ["tavily"], "要深度直接走深度"
    mcp.save({})
    with pytest.raises(ToolError):
        await web.web_search(ctx, "x")


async def test_chat_key_never_stored(fresh_db, monkeypatch):
    """主人在对话里贴 key：消息、模型、记忆里都只见占位符；Yip 用工具写进设置。"""
    seen = []

    async def fake_tools(messages, tools, **k):
        seen.append(json.dumps(messages, ensure_ascii=False))
        if any(m.get("role") == "tool" for m in messages):
            return {"role": "assistant", "content": "Tavily 接好了，试连通了"}
        ph = next(m["content"] for m in reversed(messages) if m["role"] == "user").split("key：")[-1]
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "add_connector", "arguments": json.dumps({"preset": "tavily", "values": {"key": ph}},
                                                             ensure_ascii=False)}}]}

    async def fake_test(name, cfg):
        assert KEY in cfg["url"]
        return {"ok": True, "tools": [{"name": "tavily_search"}]}
    from server.tools import mcp

    async def no_model(*a, **k):
        raise llm.LLMError("不调真模型")
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(settings, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat_tools", fake_tools)
    monkeypatch.setattr(llm, "chat", no_model)
    monkeypatch.setattr(llm, "chat_json", no_model)
    monkeypatch.setattr(mcp, "test", fake_test)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = (await c.post("/api/say", json={"text": f"好的，key：{KEY}"})).json()
            assert r["kind"] == "chat"
            t0 = time.monotonic()
            while time.monotonic() - t0 < 5 and not db.q("SELECT * FROM messages WHERE role='assistant'"):
                await asyncio.sleep(0.05)
    assert web.deep_search() == ("tavily", KEY), "key 存进了连接器"
    dump = json.dumps(db.q("SELECT * FROM messages"), ensure_ascii=False) + json.dumps(db.q("SELECT * FROM memories"), ensure_ascii=False)
    assert KEY not in dump and "【密钥 tvly-…XyZ9】" in dump
    assert seen and all(KEY not in s for s in seen), "模型从来没见过完整的 key"
    assert "通了" in db.q("SELECT content FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1")[0]["content"]


async def test_fetch_falls_back_to_keenable(fresh_db, monkeypatch):
    """前端渲染的空壳页面（正文不到 300 字）换 Keenable 抓取。"""
    class R:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body><div id=app></div></body></html>"

    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return R()

    async def kf(url):
        return "# 标题\n正文很长"
    monkeypatch.setattr(web.httpx, "AsyncClient", C)
    monkeypatch.setattr(web, "keenable_fetch", kf)
    out = await web.web_fetch(Ctx("t", "m", "w", settings.workspace()), "https://spa.example.com")
    assert "正文很长" in out
