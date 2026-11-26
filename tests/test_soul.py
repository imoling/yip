"""Yip 的灵魂：内置规矩 + 人设卡；对话里能改名、改性格，纠正一次记成规矩；白天偶尔主动说两句（默认关）。"""
import datetime as dt

from server import db, events, llm, memory, settings, setup, soul
from server.router import front

MON_10 = dt.datetime(2026, 11, 23, 10, 0)   # 星期一
SAT_10 = dt.datetime(2026, 11, 28, 10, 0)


def test_block_has_card_core_and_rules(fresh_db):
    assert soul.name() == "Yip"
    settings.save({"yip": {"soul": {"name": "茉莉", "call_me": "王总", "tone": "cheeky", "relation": "partner"}}})
    memory.add("persona", "回答控制在两句以内", source="feedback")
    b = soul.block(MON_10)
    assert b.startswith("你是茉莉，主人的搭档") and "嘴欠心软" in b and "称呼主人「王总」" in b
    assert "不用破折号" in b and "2026-11-23 星期一（工作日）上午 10:00" in b
    assert "主人纠正过的" in b and "回答控制在两句以内" in b
    assert "照实说是，不撒谎" in b, "通用版：认真问是不是 AI 不撒谎"
    assert memory.assistant_name() == "茉莉"


def test_card_rejects_unknown_values(fresh_db):
    settings.save({"yip": {"soul": {"tone": "邪魅", "relation": "老公", "emoji": "many", "name": "一二三四五六七八九十十一十二十三"}}})
    c = soul.card()
    assert c["tone"] == "steady" and c["relation"] == "assistant" and c["emoji"] == "few" and len(c["name"]) == 12


def test_tidy():
    assert soul.tidy("好的——我这就去办…。") == "好的，我这就去办"
    assert soul.tidy("文件在 ~/Documents/Yip/a.md。") == "文件在 ~/Documents/Yip/a.md。", "带路径的不动"
    assert soul.tidy("1. 第一条。\n- 第二条。") == "1. 第一条。\n- 第二条。", "列表行不动"


def test_names_from_memory(fresh_db):
    memory.add("persona", "助理名叫 Yip，自称主人的宝，随叫随到")
    assert soul.name() == "Yip", "「随叫随到」不是名字"
    assert memory.quick("以后叫你小茉") and soul.card()["name"] == "小茉", "改名同步到人设卡"


async def test_chat_tools_change_persona_and_learn(fresh_db):
    out = await setup.call("set_persona", {"name": "阿福", "call_me": "老板", "tone": "warm"})
    assert "名字 阿福" in out and "性格 温和" in out
    assert soul.card()["asked"], "改过就不用再问起名"
    assert "记下了" in await setup.call("add_style_rule", {"rule": "别叫我老板，叫我王总"})
    assert "别叫我老板，叫我王总" in soul.block()


async def test_reply_uses_soul_and_asks_name_once(fresh_db, monkeypatch):
    seen = []

    async def fake_tools(msgs, tools, **k):
        seen.append(msgs[0]["content"])
        return {"content": "在呢——有事说。"}
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat_tools", fake_tools)
    assert await front.reply("在吗") == "在呢，有事说"
    assert seen[0].startswith("你是Yip") and "还没给你起名字" in seen[0]
    await front.reply("在吗")
    assert "还没给你起名字" not in seen[1], "起名只问一次"


def test_nudge_due_rules(fresh_db):
    assert not soul.due(MON_10, 0.0), "默认关"
    settings.save({"yip": {"soul": {"proactive": True}}})
    assert soul.due(MON_10, 0.01)
    assert not soul.due(MON_10, 0.5), "掷不中就不说，时间点不固定"
    assert not soul.due(SAT_10, 0.0) and not soul.due(MON_10.replace(hour=20), 0.0), "周末、晚上不找"
    m = events.message(None, "assistant", "午饭吃了没", nudge=True)
    db.ex("UPDATE messages SET created_at=? WHERE id=?", (int(MON_10.timestamp() * 1000) - 3600_000, m["id"]))
    assert not soul.due(MON_10, 0.0), "上一句主动的话没人回，不追"
    u = events.message(None, "user", "吃了")
    db.ex("UPDATE messages SET created_at=? WHERE id=?", (int(MON_10.timestamp() * 1000) - 3000_000, u["id"]))
    assert soul.due(MON_10, 0.0)
    m2 = events.message(None, "assistant", "下午那个会别忘了", nudge=True)
    db.ex("UPDATE messages SET created_at=? WHERE id=?", (int(MON_10.timestamp() * 1000) - 2400_000, m2["id"]))
    u2 = events.message(None, "user", "好")
    db.ex("UPDATE messages SET created_at=? WHERE id=?", (int(MON_10.timestamp() * 1000) - 1800_000, u2["id"]))
    assert not soul.due(MON_10, 0.0), "一天最多两次"
