# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""长期跟进（Goals）与轻量后台复盘（Dreaming）。

从「办完一件事」升级到「持续推进一个长期目标」：
- 每条目标有状态（active 进行中 / paused 已暂停 / done 已完成）、起始时间、已持续天数、关联任务与产物、可选定期复盘规则。
- 到点复盘时把历史进展记忆和最近一次结果带进去，让队员基于前情接着推进，而不是每次从零重来。
"""
from __future__ import annotations

import asyncio
import datetime as dt

from . import automation, db, events, memory

STATUSES = ("active", "paused", "done")
TICK = 60


def _ms(d: dt.datetime | None) -> int | None:
    return int(d.timestamp() * 1000) if d else None


def _enrich(r: dict | None) -> dict | None:
    if not r:
        return None
    rule = r.get("rule") if isinstance(r.get("rule"), dict) else None
    days = max(1, int((db.now() - r["started_at"]) // (86400 * 1000)) + 1)
    jobs = db.q("SELECT id, title, status, updated_at, note FROM tasks WHERE parent_id IS NULL AND goal_id=? ORDER BY created_at DESC LIMIT 10",
                (r["id"],))
    arts = db.q(
        "SELECT a.id, a.name, a.kind, a.path, a.created_at FROM artifacts a "
        "JOIN tasks t ON a.main_id = t.id WHERE t.goal_id=? ORDER BY a.id DESC LIMIT 12",
        (r["id"],),
    )
    mems = db.q("SELECT id, kind, content, created_at FROM memories WHERE goal_id=? ORDER BY id DESC LIMIT 12",
                (r["id"],))
    return {
        **r,
        "rule": rule,
        "when": automation.describe(rule) if rule else "",
        "days": days,
        "jobs": jobs,
        "artifacts": arts,
        "memories": mems,
        "job_count": len(jobs),
    }


def listing(status: str = "") -> list[dict]:
    if status in STATUSES:
        rows = db.q("SELECT * FROM goals WHERE status=? ORDER BY updated_at DESC", (status,))
    else:
        rows = db.q("SELECT * FROM goals ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, updated_at DESC")
    return [_enrich(r) for r in rows]


def get(gid: str) -> dict | None:
    return _enrich(db.q1("SELECT * FROM goals WHERE id=?", (gid,)))


def save(body: dict, gid: str | None = None) -> dict:
    title = str(body.get("title") or "").strip()[:32]
    target = str(body.get("target") or "").strip()
    if not target:
        raise ValueError("写清楚要持续跟进什么目标")
    if not title:
        title = target.split("，")[0].split("。")[0][:16].strip() or "长期目标"
    status = str(body.get("status") or "active")
    if status not in STATUSES:
        status = "active"
    raw_rule = body.get("rule")
    rule = automation.validate(raw_rule) if isinstance(raw_rule, dict) and raw_rule.get("type") else None
    nxt = _ms(automation.next_time(rule, dt.datetime.now())) if rule and status == "active" else None
    note = str(body.get("progress_note") or "").strip()[:500]
    now = db.now()
    if gid:
        if not db.q1("SELECT id FROM goals WHERE id=?", (gid,)):
            raise ValueError("目标不存在")
        db.update("goals", gid, title=title, target=target, status=status, rule=rule,
                  progress_note=note, next_review_at=nxt, updated_at=now)
    else:
        gid = db.new_id("g")
        db.insert("goals", id=gid, title=title, target=target, status=status, rule=rule,
                  progress_note=note, started_at=now, last_review_at=None, next_review_at=nxt,
                  last_job=None, updated_at=now)
    events.publish("goal", {"id": gid})
    return get(gid)


def set_status(gid: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError("状态不对")
    row = db.q1("SELECT * FROM goals WHERE id=?", (gid,))
    if not row:
        raise ValueError("目标不存在")
    rule = row.get("rule") if isinstance(row.get("rule"), dict) else None
    nxt = _ms(automation.next_time(rule, dt.datetime.now())) if rule and status == "active" else None
    db.update("goals", gid, status=status, next_review_at=nxt, updated_at=db.now())
    events.publish("goal", {"id": gid})
    return get(gid)


def remove(gid: str) -> None:
    db.ex("DELETE FROM goals WHERE id=?", (gid,))
    events.publish("goal", {"id": gid, "deleted": True})


def needs_plan(g: dict) -> bool:
    """目标刚建立、还没有已完成的规划任务且没有进展记录：先和主人对齐条件、制定落地计划。"""
    if (g.get("progress_note") or "").strip():
        return False
    done_jobs = [j for j in (g.get("jobs") or []) if j.get("status") == "done"]
    return not done_jobs


def context_prompt(g: dict) -> str:
    """把目标的历史进展与关联记忆拼成两阶段指令：首次先追问对齐并出落地计划，后续基于计划复盘推进。"""
    parts = [f"【长期目标：{g['title']}（已持续第 {g['days']} 天）】\n目标：{g['target']}"]
    if g.get("progress_note"):
        parts.append(f"当前计划与进展：{g['progress_note']}")
    if g.get("memories"):
        parts.append("已记下的条件与进展：\n" + "\n".join(f"- {m['content']}" for m in g["memories"][:6]))
    if g.get("artifacts"):
        names = "、".join(a["name"] for a in g["artifacts"][:4])
        parts.append(f"已有文档：{names}")
    if needs_plan(g):
        parts.append(
            "【阶段：首次规划，先对齐再出容易落地的方案】\n"
            "1. 先检查目标里缺少哪些决定方案能否落地的关键信息（如当前基线、每天/每周可投入时间、作息与现实限制、偏好节奏或底线）；"
            "只要缺关键信息，务必先用 ask_user 向主人追问补充（每次 1 个聚焦问题、2–5 个贴合现实的选项，最多追问 2 轮），不要靠猜直接套通用模板。\n"
            "2. 收集到主人的现实条件后，再给出一份容易落地、今天就能开始的 Markdown 行动方案（除非目标明确要 Word，否则只写 .md，不额外生成 Word）：\n"
            "   - 现实评估：目标节奏是否过激，给出更稳妥的分阶段里程碑（按周/节点、可量化）；\n"
            "   - 日常最小可执行动作：具体到每天/每周做什么、花多久、怎么低门槛坚持；\n"
            "   - 阻碍预案：遇到加班、应酬或中断一天时怎么无缝接上；\n"
            "   - 下次复盘要核对的 2–3 个具体指标。"
        )
    else:
        if g.get("jobs"):
            last = g["jobs"][0]
            parts.append(f"上次执行：「{last['title']}」（{last['status']}）")
        parts.append(
            "【阶段：持续跟进与复盘】\n"
            "请对照已定计划与阶段检查点推进下一步：如需向主人核对近期实际进度或卡点，先用 ask_user 简短问一句，"
            "再给出下一阶段最关键的 1–3 个具体动作与微调建议；没有变化就一句话说明。"
        )
    return "\n\n".join(parts)


def record_progress(gid: str, summary: str, *, task_id: str | None = None) -> None:
    """关联目标的任务交付后，把计划摘要或最新进展回写到目标，供下次跟进接着推进。"""
    row = db.q1("SELECT id, title FROM goals WHERE id=?", (gid,))
    text = (summary or "").strip()
    if not row or not text:
        return
    note = text.splitlines()[0].strip()[:480]
    db.update("goals", gid, progress_note=note, updated_at=db.now())
    memory.add("state", f"目标「{row['title']}」进展：{note[:180]}", source="job", task_id=task_id or "", goal_id=gid)
    events.publish("goal", {"id": gid})


async def review(gid: str, start) -> dict:
    """触发一次有状态目标规划或复盘。start(text, shown, goal_id) 由 main 传入。"""
    g = get(gid)
    if not g:
        raise ValueError("目标不存在")
    planning = needs_plan(g)
    prompt = context_prompt(g)
    shown = f"🎯 规划「{g['title']}」：对齐条件并制定落地计划" if planning else f"🎯 跟进「{g['title']}」：检查进展与下一步"
    job = start(prompt, shown, gid)
    rule = g.get("rule") if isinstance(g.get("rule"), dict) else None
    nxt = _ms(automation.next_time(rule, dt.datetime.now())) if rule and g["status"] == "active" else None
    db.update("goals", gid, last_review_at=db.now(), last_job=job["id"], next_review_at=nxt, updated_at=db.now())
    events.publish("goal", {"id": gid})
    return job


async def tick(start) -> int:
    n = 0
    for r in db.q("SELECT id FROM goals WHERE status='active' AND next_review_at IS NOT NULL AND next_review_at<=?",
                  (db.now(),)):
        try:
            await review(r["id"], start)
            n += 1
        except Exception:  # noqa: BLE001
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
