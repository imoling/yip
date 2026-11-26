"""按能力卡片给子任务挑队员：先问模型，模型不可用时按能力描述的文字重合打分。"""
from __future__ import annotations

import asyncio
import json

from .. import llm, textsim
from . import registry
from .package import WorkerCard

TIMEOUT = 10
THRESHOLD = 1


def _route_text(card: WorkerCard) -> str:
    return card.text() + " " + card.prompt.split("## 需要的输入")[0]


def focus(item: dict) -> str:
    """子任务目标里常带着整件事的目标（「…——这一块只负责 X」），匹配只看标题和这一块。"""
    goal = item.get("goal", "") or ""
    return f"{item.get('title', '')} {goal.split('——', 1)[1] if '——' in goal else goal}"


def rule_match(item: dict, cards: list[WorkerCard]) -> dict:
    text = focus(item)
    best, best_score, best_skill = None, 0, None
    for c in cards:
        score = textsim.overlap(text, _route_text(c))
        if score > best_score:
            best, best_score = c, score
            best_skill = max(c.skills or c.demos, key=lambda s: textsim.overlap(text, s.match_text), default=None)
    if best is None or best_score < THRESHOLD:
        g = registry.general()
        return {"worker_id": g.id, "reason": "没有对口的专职队员，交给通用助理", "source": "rule"}
    reason = f"会「{best_skill.name}」" if best_skill else f"能力和「{item.get('title')}」对口"
    return {"worker_id": best.id, "reason": reason, "source": "rule"}


PROMPT = """你在给子任务挑队员。按能力卡片匹配，不按名字猜。
规则：
- 每个子任务挑一个最对口的队员，理由一句话，引用卡片里的能力或技能名，例如「会『检索竞品报价』」
- 没有对口的就选 {general}，理由写「没有对口的专职队员」
- worker_id 只能从卡片里选
只输出 JSON：{{"assignments": [{{"index": 0, "worker_id": "...", "reason": "..."}}]}}

能力卡片：
{cards}

子任务：
{items}"""


async def match(items: list[dict], cards: list[WorkerCard] | None = None) -> list[dict]:
    cards = cards or registry.active()
    ids = {c.id for c in cards}
    out = [rule_match(it, cards) for it in items]
    if not llm.configured() or not items:
        return out
    try:
        r = await asyncio.wait_for(llm.chat_json([{"role": "user", "content": PROMPT.format(
            general=registry.GENERAL_ID,
            cards=json.dumps([c.summary() for c in cards], ensure_ascii=False),
            items=json.dumps([{"index": i, "title": it.get("title"), "goal": it.get("goal")} for i, it in enumerate(items)], ensure_ascii=False),
        )}], fast=True, timeout=TIMEOUT), TIMEOUT)
        for a in r.get("assignments", []):
            i = a.get("index")
            if isinstance(i, int) and 0 <= i < len(items) and a.get("worker_id") in ids:
                out[i] = {"worker_id": a["worker_id"], "reason": str(a.get("reason", ""))[:60], "source": "model"}
    except Exception:
        pass
    return out
