"""定时自动化：到点把一句话交给 Yip，和你亲口说的一样走拆活、派活、放行。

规则（rule）是一个小字典：
    {"type": "daily", "time": "08:00"}
    {"type": "weekdays", "time": "08:00"}              周一到周五
    {"type": "weekly", "time": "17:00", "days": [5]}   1=周一 … 7=周日
    {"type": "monthly", "time": "09:00", "day": 1}
    {"type": "interval", "hours": 2}                   每隔几小时
    {"type": "once", "at": "2026-11-20 15:00"}         只一次

后端起一个协程每 30 秒看一次有没有到点的。电脑睡过头错过了好几次，醒来只补跑一次，再从现在往后排。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re

from . import db, events, llm

SCHEMA = """CREATE TABLE IF NOT EXISTS automations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, rule TEXT NOT NULL, text TEXT NOT NULL,
  enabled INTEGER DEFAULT 1, last_at INTEGER, next_at INTEGER, last_job TEXT, created_at INTEGER NOT NULL)"""
TICK = 30
WEEK = "一二三四五六日"


def _ensure() -> None:
    db.ex(SCHEMA)


def _hm(t: str) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2})[:：](\d{2})$", (t or "").strip())
    if not m:
        raise ValueError("时间要写成 08:00 这样")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError("时间不对")
    return h, mi


def validate(rule: dict) -> dict:
    t = rule.get("type")
    if t == "interval":
        h = float(rule.get("hours") or 0)
        if h < 0.05:
            raise ValueError("间隔至少 3 分钟")
        return {"type": t, "hours": h}
    if t == "once":
        dt.datetime.strptime(rule.get("at", ""), "%Y-%m-%d %H:%M")
        return {"type": t, "at": rule["at"]}
    if t not in ("daily", "weekdays", "weekly", "monthly"):
        raise ValueError("不认识的频率")
    _hm(rule.get("time", ""))
    out = {"type": t, "time": rule["time"]}
    if t == "weekly":
        days = sorted({int(d) for d in rule.get("days") or [] if 1 <= int(d) <= 7})
        if not days:
            raise ValueError("每周要选星期几")
        out["days"] = days
    if t == "monthly":
        day = int(rule.get("day") or 0)
        if not 1 <= day <= 31:
            raise ValueError("每月几号要在 1 到 31 之间")
        out["day"] = day
    return out


def next_time(rule: dict, after: dt.datetime) -> dt.datetime | None:
    """严格晚于 after 的下一次；只一次的过了就返回 None。"""
    t = rule["type"]
    if t == "interval":
        return after + dt.timedelta(hours=rule["hours"])
    if t == "once":
        at = dt.datetime.strptime(rule["at"], "%Y-%m-%d %H:%M")
        return at if at > after else None
    h, mi = _hm(rule["time"])
    day = after.replace(hour=h, minute=mi, second=0, microsecond=0)
    for i in range(0, 400):
        cand = day + dt.timedelta(days=i)
        if cand <= after:
            continue
        wd = cand.isoweekday()
        if t == "daily" or (t == "weekdays" and wd <= 5) or (t == "weekly" and wd in rule["days"]) \
                or (t == "monthly" and cand.day == rule["day"]):
            return cand
    return None


def describe(rule: dict) -> str:
    t = rule["type"]
    if t == "interval":
        h = rule["hours"]
        return f"每隔 {int(h * 60)} 分钟" if h < 1 else f"每隔 {h:g} 小时"
    if t == "once":
        return f"只在 {rule['at']}"
    if t == "daily":
        return f"每天 {rule['time']}"
    if t == "weekdays":
        return f"工作日 {rule['time']}"
    if t == "weekly":
        return f"每周{'、'.join(WEEK[d - 1] for d in rule['days'])} {rule['time']}"
    return f"每月 {rule['day']} 号 {rule['time']}"


def _ms(d: dt.datetime | None) -> int | None:
    return int(d.timestamp() * 1000) if d else None


def _row(r: dict) -> dict:
    rule = r["rule"] if isinstance(r["rule"], dict) else json.loads(r["rule"])
    job = db.task(r["last_job"]) if r.get("last_job") else None
    return {**r, "rule": rule, "when": describe(rule), "last_status": job["status"] if job else None,
            "last_title": job["title"] if job else None}


def listing() -> list[dict]:
    _ensure()
    return [_row(r) for r in db.q("SELECT * FROM automations ORDER BY id DESC")]


def get(aid: int) -> dict | None:
    _ensure()
    r = db.q1("SELECT * FROM automations WHERE id=?", (aid,))
    return _row(r) if r else None


def save(body: dict, aid: int | None = None) -> dict:
    _ensure()
    rule = validate(body.get("rule") or {})
    text = str(body.get("text") or "").strip()
    if not text:
        raise ValueError("要写到点做什么")
    name = str(body.get("name") or "").strip() or text[:16]
    enabled = int(bool(body.get("enabled", True)))
    nxt = _ms(next_time(rule, dt.datetime.now())) if enabled else None
    cols = {"name": name, "rule": json.dumps(rule, ensure_ascii=False), "text": text, "enabled": enabled, "next_at": nxt}
    if aid:
        db.update("automations", aid, **cols)
    else:
        aid = db.insert("automations", created_at=db.now(), **cols)
    events.publish("automation", {"id": aid})
    return get(aid)


def toggle(aid: int, on: bool) -> dict:
    a = get(aid)
    nxt = _ms(next_time(a["rule"], dt.datetime.now())) if on else None
    db.update("automations", aid, enabled=int(on), next_at=nxt)
    events.publish("automation", {"id": aid})
    return get(aid)


def remove(aid: int) -> None:
    _ensure()
    db.ex("DELETE FROM automations WHERE id=?", (aid,))
    events.publish("automation", {"id": aid, "deleted": True})


async def fire(aid: int, start) -> dict:
    """跑一次：start(text, shown) 由 main 传进来（就是 create_main），避免循环导入。"""
    a = get(aid)
    shown = f"⏰ 自动化「{a['name']}」：{a['text']}"
    job = start(a["text"], shown)
    now = dt.datetime.now()
    once = a["rule"]["type"] == "once"
    nxt = next_time(a["rule"], now) if a["enabled"] and not once else None
    db.update("automations", aid, last_at=db.now(), last_job=job["id"], next_at=_ms(nxt),
              enabled=0 if once else a["enabled"])
    events.publish("automation", {"id": aid})
    return job


async def tick(start) -> int:
    """跑掉所有到点的，返回跑了几条。错过多次的只补一次：fire 会从现在往后排下一次。"""
    _ensure()
    n = 0
    for r in db.q("SELECT id FROM automations WHERE enabled=1 AND next_at IS NOT NULL AND next_at<=?", (db.now(),)):
        try:
            await fire(r["id"], start)
            n += 1
        except Exception:  # noqa: BLE001 — 一条坏了不能拖垮别的
            pass
    return n


async def loop(start) -> None:
    while True:
        try:
            await tick(start)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(TICK)


# ---------- 一句话 → 规则 ----------
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
          "十一": 11, "十二": 12, "日": 7, "天": 7}


def _num(s: str) -> int:
    return int(s) if s.isdigit() else CN_NUM.get(s, 0)


def rule_parse(text: str) -> dict | None:
    t = text.strip()
    tm = re.search(r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:[:：点时])\s*(半|(\d{1,2})分?)?", t)
    hour, minute = 9, 0
    if tm:
        hour = _num(tm.group(2)) % 24
        if tm.group(1) in ("下午", "晚上") and hour < 12:
            hour += 12
        minute = 30 if tm.group(3) == "半" else int(tm.group(4) or 0)
    time_s = f"{hour:02d}:{minute:02d}"
    m = re.search(r"每隔?\s*(\d+|[一二两三四五六七八九十]+)\s*(个)?\s*(小时|分钟)", t)
    if m:
        n = _num(m.group(1))
        rule = {"type": "interval", "hours": n if m.group(3) == "小时" else n / 60}
    elif re.search(r"工作日|周一到周五|每个工作日", t):
        rule = {"type": "weekdays", "time": time_s}
    elif m := re.search(r"每(?:周|星期|礼拜)([一二三四五六日天、和]+)", t):
        days = sorted({CN_NUM[c] for c in m.group(1) if c in CN_NUM})
        rule = {"type": "weekly", "time": time_s, "days": days}
    elif m := re.search(r"每月\s*(\d{1,2}|[一二三四五六七八九十]+)\s*[号日]", t):
        rule = {"type": "monthly", "time": time_s, "day": _num(m.group(1))}
    elif re.search(r"每天|每日|天天|每晚|每早", t):
        rule = {"type": "daily", "time": time_s}
    else:
        return None
    body = re.sub(r"^(?:请|帮我)?(每隔?\s*\S*?(?:小时|分钟)|每个?工作日|周一到周五|每(?:周|星期|礼拜)[一二三四五六日天、和]+|每月\s*\S+?[号日]|每天|每日|天天|每晚|每早)"
                  r"[，,\s]*(?:(?:早上|上午|中午|下午|晚上)?\s*\S{1,3}[:：点时](?:半|\d{1,2}分?)?)?[，,\s]*", "", t)
    return {"rule": rule, "text": body or t, "name": (body or t)[:14]}


PARSE = """把主人的一句话拆成定时任务。只输出 JSON：
{{"name": "≤12 字的名字", "rule": 规则, "text": "到点要交给助理去做的事，一句完整的话，不含时间"}}
规则只能是这几种之一：
{{"type": "daily", "time": "HH:MM"}} / {{"type": "weekdays", "time": "HH:MM"}} / {{"type": "weekly", "time": "HH:MM", "days": [1-7，1 是周一]}} /
{{"type": "monthly", "time": "HH:MM", "day": 1-31}} / {{"type": "interval", "hours": 小时数可带小数}} / {{"type": "once", "at": "YYYY-MM-DD HH:MM"}}
没说时间的默认 09:00。现在是 {now}。
主人：{text}"""


async def parse(text: str) -> dict:
    if llm.configured():
        try:
            r = await llm.chat_json([{"role": "user", "content": PARSE.format(
                text=text, now=dt.datetime.now().strftime("%Y-%m-%d %H:%M %A"))}], fast=True, timeout=20)
            rule = validate(r.get("rule") or {})
            return {"name": str(r.get("name") or "")[:16], "rule": rule, "text": str(r.get("text") or text),
                    "when": describe(rule)}
        except Exception:  # noqa: BLE001
            pass
    got = rule_parse(text)
    if not got:
        raise ValueError("没听出什么时候做，比如说「每个工作日早上 8 点…」")
    got["rule"] = validate(got["rule"])
    got["when"] = describe(got["rule"])
    return got
