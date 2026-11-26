"""规则兜底与成本门槛：不依赖网络，必须全过。"""
import asyncio

import pytest

from server import llm
from server.router import classify
from tests.conftest import seed_context


def test_cost_and_confirm_threshold():
    assert classify.cost_level(0.1) == "low"
    assert classify.cost_level(0.3) == "mid"
    assert classify.cost_level(0.7) == "mid"
    assert classify.cost_level(0.71) == "high"
    assert not classify.need_confirm("overturn", 0.64)
    assert classify.need_confirm("overturn", 0.76)
    assert not classify.need_confirm("augment", 0.99)


@pytest.mark.parametrize("text,intent,label", [
    ("别做定价了，改看服务政策", "overturn", "A"),
    ("全部重来", "overturn", "main"),
    ("定价不用做了", "overturn", "A"),
    ("顺便再加一家竞品", "append", "main"),
    ("另外看看三家最近有没有融资", "append", "main"),
    ("定价那块重点看企业版，别管个人版", "augment", "A"),
    ("渠道也看一下华东的经销商", None, "B"),
])
async def test_rule_classify(fresh_db, text, intent, label):
    ids = seed_context()
    d = await classify.classify(text, ids["main"])
    assert d.source in ("rule", "fallback")
    if intent:
        assert d.intent == intent
    assert d.target == ids[label]


async def test_overturn_detail_and_confirm(fresh_db):
    ids = seed_context(progress=(0.76, 0.4, 0.3))
    d = await classify.classify("别做定价了，改看服务政策", ids["main"])
    assert d.detail == "服务政策"
    assert d.cost == "high" and d.need_confirm


async def test_unknown_falls_back_to_append(fresh_db):
    ids = seed_context()
    d = await classify.classify("这个不对", ids["main"])
    assert d.intent == "append" and d.unsure and d.confidence == 0.3


async def test_model_timeout_uses_rules(fresh_db, monkeypatch):
    ids = seed_context()
    monkeypatch.setattr(llm, "configured", lambda: True)

    async def slow(*a, **k):
        await asyncio.sleep(5)
    monkeypatch.setattr(llm, "chat_json", slow)
    t0 = asyncio.get_event_loop().time()
    d = await classify.classify("别做定价了", ids["main"])
    assert asyncio.get_event_loop().time() - t0 < classify.timeout() + 0.5
    assert d.intent == "overturn" and d.source == "rule"


async def test_model_low_confidence_becomes_append(fresh_db, monkeypatch):
    ids = seed_context()
    monkeypatch.setattr(llm, "configured", lambda: True)

    async def fake(*a, **k):
        return {"intent": "overturn", "target": "t_a", "reason": "像是换方向", "confidence": 0.4}
    monkeypatch.setattr(llm, "chat_json", fake)
    d = await classify.classify("定价这块要不再想想", ids["main"])
    assert d.intent == "append" and d.unsure and d.source == "model"


async def test_model_invalid_output_uses_rules(fresh_db, monkeypatch):
    ids = seed_context()
    monkeypatch.setattr(llm, "configured", lambda: True)

    async def fake(*a, **k):
        return {"intent": "cancel", "target": "t_a"}
    monkeypatch.setattr(llm, "chat_json", fake)
    d = await classify.classify("顺便再加一家竞品", ids["main"])
    assert d.intent == "append" and d.source == "rule"


async def test_model_path_used_when_ok(fresh_db, monkeypatch):
    ids = seed_context()
    monkeypatch.setattr(llm, "configured", lambda: True)

    async def fake(*a, **k):
        return {"intent": "augment", "target": "t_b", "detail": "渠道加看华东经销商", "reason": "渠道已在做，加关注点", "confidence": 0.86}
    monkeypatch.setattr(llm, "chat_json", fake)
    d = await classify.classify("顺便把渠道也看一下", ids["main"])
    assert (d.intent, d.target, d.source, d.detail) == ("augment", "t_b", "model", "渠道加看华东经销商")


async def test_overturn_takes_new_goal_after_keyword(fresh_db):
    """「算了，做个推箱子吧」：推翻后按推箱子重拆，不是按原来的贪吃蛇。"""
    ids = seed_context()
    d = await classify.classify("算了，做个推箱子吧", ids["main"])
    assert d.intent == "overturn" and d.detail == "做个推箱子"
    d = await classify.classify("算了", ids["main"])
    assert d.intent == "overturn" and not d.detail


def test_timeout_waits_for_main_model_without_fast(fresh_db, monkeypatch):
    from server import settings
    base = settings.load()
    monkeypatch.setattr(settings, "load", lambda: {**base, "fast_model": ""})
    assert classify.timeout() >= 12
    monkeypatch.setattr(settings, "load", lambda: {**base, "fast_model": "x:z"})
    assert classify.timeout() == float(settings.yip()["classify_timeout"])
