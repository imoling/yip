"""IntentRouter：判断一句插话是补充、追加还是推翻，并给出成本。不执行动作。

顺序：快模型（超时见设置，默认 3 s）→ 失败、超时、非法输出时走本地规则 → 规则也没命中按追加、标没把握。
"""
from __future__ import annotations

import asyncio
import json
import re

from .. import db, llm, settings, textsim
from ..schema import Decision

# 三个门槛在「设置 · 通用」里可改，默认值见 settings.YIP_DEFAULTS
def timeout() -> float:
    """没配快模型时用主模型判断，一次要 3 到 5 秒：至少等 12 秒，免得每次都退回规则。"""
    t = float(settings.yip()["classify_timeout"])
    return t if settings.load().get("fast_model") else max(t, 12.0)


def low_confidence() -> float:
    return float(settings.yip()["low_confidence"])


def confirm_above() -> float:
    return float(settings.yip()["overturn_confirm"])

OVERTURN = ["全部重做", "方向不对", "换个方向", "不用做", "不查了", "别做", "重来", "算了", "不做了"]
APPEND = ["补充一下", "顺便", "再加", "另外", "还有"]
AUGMENT = ["改成", "格式", "重点", "优先", "不要", "只"]
WEIGHT = {"augment": 0, "append": 1, "overturn": 2}
INTENT_CN = {"augment": "补充", "append": "追加", "overturn": "推翻"}


def cost_level(progress: float) -> str:
    return "low" if progress < 0.3 else ("mid" if progress <= confirm_above() else "high")


def need_confirm(intent: str, progress: float) -> bool:
    return intent == "overturn" and progress > confirm_above()


# ---------- 上下文 ----------
def context(main_id: str | None) -> dict:
    main = db.task(main_id) if main_id else None
    if not main:
        return {"main": None, "tasks": []}
    kids = db.children(main["id"])
    return {
        "main": {"id": main["id"], "title": main["title"], "goal": main["goal"], "status": main["status"],
                 "progress": round(main["progress"], 2)},
        "tasks": [{
            "id": k["id"], "label": k["label"], "title": k["title"], "goal": k["goal"], "status": k["status"],
            "progress": round(k["progress"], 2), "constraints": k["constraints"] or [],
            "recent_events": db.recent_events(k["id"]),
        } for k in kids],
    }


# ---------- 规则 ----------
def _hits(text: str, words: list[str]) -> list[str]:
    return sorted([w for w in words if w in text], key=len, reverse=True)


def pick_target(text: str, ctx: dict) -> str | None:
    """找这句话说的是哪个子任务：标签（「A」「任务 B」）优先，其次标题、目标的文字重合。"""
    live = [t for t in ctx["tasks"] if t["status"] != "cancelled"]
    for t in live:
        if re.search(rf"(?<![A-Za-z]){t['label']}(?![A-Za-z])", text):
            return t["id"]
    # 只比各子任务自己独有的字眼：整件事的标题（如「三家竞品简报」）里的词谁都沾边，不算
    common = textsim.grams(ctx["main"]["title"]) if ctx["main"] else set()
    said = textsim.grams(text) - common
    best, score = None, 0
    for t in live:
        goal = t["goal"] or ""
        own = textsim.grams(t["title"] + " " + (goal.split("——", 1)[1] if "——" in goal else "")) - common
        s = len(said & own)
        if s > score:
            best, score = t, s
    if best and score >= 1:
        return best["id"]
    return ctx["main"]["id"] if ctx["main"] else None


def _after(text: str, marks: list[str]) -> str | None:
    for m in marks:
        i = text.find(m)
        if i >= 0:
            rest = text[i + len(m):].strip(" ，。,；;")
            if rest:
                return rest
    return None


NEGATE = re.compile(r"(?:别管|不看|不要|不用管|不管)([^，。,；;了]+)")
ALSO = re.compile(r"([^，。,；;]{1,8}?)(?:那边|那块)?也(?:看|查|看看|查查)")


def topic_of(x: str, ctx: dict) -> str | None:
    """x 说的是整件事还是哪个子任务的主题；都不是返回 None（说明只是其中一个子集）。"""
    if not ctx["main"]:
        return None
    main_g = textsim.grams(ctx["main"]["title"])
    xg = textsim.grams(x)
    if xg & main_g:
        return ctx["main"]["id"]
    for t in ctx["tasks"]:
        goal = t["goal"] or ""
        own = textsim.grams(t["title"] + " " + (goal.split("——", 1)[1] if "——" in goal else "")) - main_g
        if xg & own and t["status"] != "cancelled":
            return t["id"]
    return None


def rule_classify(text: str, ctx: dict) -> Decision | None:
    target = pick_target(text, ctx)
    o, a, g = _hits(text, OVERTURN), _hits(text, APPEND), _hits(text, AUGMENT)
    new_goal = _after(text, ["改看", "改成", "换成", "改为", "改做", "换个方向", "改查"])
    if o and not new_goal:  # 「算了，做个推箱子吧」：推翻词后面跟着的就是新目标
        rest = _after(text, [o[0]])
        rest = re.sub(r"(?:吧|吧。|啊|了)$", "", rest or "").strip(" ，。,；;！!")
        if rest and len(rest) >= 3 and re.search(r"做|写|开发|查|整理|弄|搞|来|换|改|调研|分析", rest):
            new_goal = rest
    if o:
        return Decision(intent="overturn", target=target, confidence=0.95, source="rule",
                        reason=f"命中「{o[0]}」，按推翻处理", detail=new_goal)
    m = NEGATE.search(text)
    if m:  # 砍整件事 / 整个子任务 → 推翻；只砍其中一部分 → 补充
        hit = topic_of(m.group(1), ctx)
        if hit:
            return Decision(intent="overturn", target=hit, confidence=0.8, source="rule",
                            reason=f"不要的是「{m.group(1)}」整块，按推翻处理", detail=new_goal)
        return Decision(intent="augment", target=target, confidence=0.75, source="rule",
                        reason=f"只是不要「{m.group(1)}」这部分，目标没变", detail=text)
    m = ALSO.search(text)
    if m:  # 「X 也看」：X 已经在做 → 补充；没在做 → 追加
        hit = topic_of(m.group(1), ctx)
        if hit and hit != (ctx["main"] or {}).get("id"):
            return Decision(intent="augment", target=hit, confidence=0.7, source="rule",
                            reason=f"「{m.group(1).strip()}」已经在做，加个关注点", detail=text)
    if a:
        return Decision(intent="append", target=target, confidence=0.7, source="rule",
                        reason=f"命中「{a[0]}」，按追加处理", detail=text)
    if g:
        return Decision(intent="augment", target=target, confidence=0.7, source="rule",
                        reason=f"命中「{g[0]}」，按补充处理", detail=text)
    return None


# ---------- 模型 ----------
PROMPT = """你在判断一句插话的性质。只输出 JSON，不要解释。

三类定义：
- augment（补充）：任务目标没变，只是加约束、收窄范围、改路径、改格式。例：「只看企业版」「别管个人版」「要 Markdown 不要 PPT」
- append（追加）：任务目标没变，但多了一件原来没有的事要做。例：「顺便查一下渠道」「再加一家竞品」
- overturn（推翻）：任务目标变了，或者方向被否定。例：「别做定价了」「方向不对，重来」「换个方向」

判断顺序：
1. 这句话之后，原任务的目标还算数吗？不算 → overturn
2. 算数的话，是多了一件新事，还是给原来那件事加条件？新事 → append；加条件 → augment

注意（这几组最容易判错）：
- 「别管个人版」是砍掉一个子集、加条件 → augment；「别做定价了」是否定任务本身 → overturn
- 砍掉的正好是某个子任务的整块（「功能那块没必要了」「渠道先停一下」）→ 对那个子任务是 overturn，target 选它，detail 写 null
- 「渠道也看一下」：如果任务清单里已经有在做渠道的子任务 → augment（给它加关注点）；「顺便查一下渠道」而清单里没有渠道 → append
- 「换个格式」→ augment；「换个方向」→ overturn
- 「再加一家竞品」→ append；「只看这一家」→ augment
- 拿不准就选 append，并把 confidence 调低

target 选这句话针对的任务 id：针对某个子任务就选子任务；针对整件事或说不清就选主任务。
detail：augment 写成一条约束；append 写成要新做的那件事（一句话）；overturn 写新目标，没有新目标就写 null。
reason：一句话给用户看，说清目标变没变、为什么这么判，≤30 字。

输出格式：{{"intent": "augment|append|overturn", "target": "任务 id", "detail": "...", "reason": "...", "confidence": 0.0-1.0}}

主任务：{main}
子任务：
{tasks}

插话：{text}"""


async def model_classify(text: str, ctx: dict) -> Decision:
    tasks = [{k: t[k] for k in ("id", "label", "title", "goal", "status", "progress", "constraints", "recent_events")} for t in ctx["tasks"]]
    r = await llm.chat_json([{"role": "user", "content": PROMPT.format(
        main=json.dumps(ctx["main"], ensure_ascii=False),
        tasks=json.dumps(tasks, ensure_ascii=False), text=text)}], fast=True, timeout=timeout() + 1)
    ids = {t["id"] for t in ctx["tasks"]} | ({ctx["main"]["id"]} if ctx["main"] else set())
    intent = r.get("intent")
    if intent not in WEIGHT:
        raise ValueError(f"非法 intent：{intent}")
    target = r.get("target") if r.get("target") in ids else pick_target(text, ctx)
    conf = float(r.get("confidence", 0.5))
    return Decision(intent=intent, target=target, reason=str(r.get("reason", ""))[:60],
                    confidence=max(0.0, min(conf, 1.0)), detail=r.get("detail") or None, source="model")


# ---------- 入口 ----------
def finalize(d: Decision, text: str, ctx: dict) -> Decision:
    if d.confidence < low_confidence() and d.intent != "append":
        d = Decision(intent="append", target=d.target, detail=text, source=d.source, unsure=True,
                     confidence=d.confidence, reason=f"像是{INTENT_CN[d.intent]}，但没把握，先排着")
    elif d.confidence < low_confidence():
        d.unsure = True
    if d.intent == "augment" and not d.detail:
        d.detail = text
    if d.intent == "append" and not d.detail:
        d.detail = text
    t = db.task(d.target) if d.target else None
    progress = t["progress"] if t else 0.0
    d.cost = cost_level(progress)
    d.need_confirm = need_confirm(d.intent, progress)
    return d


async def classify(text: str, main_id: str | None) -> Decision:
    ctx = context(main_id)
    d: Decision | None = None
    if llm.configured() and ctx["main"]:
        try:
            d = await asyncio.wait_for(model_classify(text, ctx), timeout())
        except Exception:  # 超时、断网、非法输出都不往上抛
            d = None
    if d is None:
        d = rule_classify(text, ctx)
        if d is None:
            d = Decision(intent="append", target=pick_target(text, ctx), confidence=0.3, source="fallback",
                         unsure=True, reason="没听出是哪一类，先排着", detail=text)
        elif llm.configured():
            d.reason = "模型没及时回应，" + d.reason
    return finalize(d, text, ctx)
