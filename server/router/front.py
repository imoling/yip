"""前台分流：和 Yip 只有一条对话，一句话进来先分三类。

- chat：闲聊或提问，Yip 直接回答，不派活
- new：一件新的活，拆解派活
- interject：针对正在做的某件事插话，再交给 classify 判补充 / 追加 / 推翻

快模型优先（超时同插话判断），失败走本地规则。
"""
from __future__ import annotations

import asyncio
import json
import re

from .. import db, llm, memory, settings, soul, textsim, uploads
from ..tools import mcp as t_mcp
from ..tools import skills as t_skills
from ..workers import registry
from . import classify

WORK = re.compile(r"帮我|整理|写一|写个|写份|做一|做个|做份|查一下|查查|调研|分析|生成|准备|起草|汇总|比较|对比|列出|翻译|总结|安排|做成|出一份|出个|规划|评估|梳理")
ASK = re.compile(r"[？?]\s*$|吗[？?。]?$|呢[？?。]?$|什么|哪[里儿个些]|怎么|多少|是不是|有没有|为什么|谁")
# 以后按时间反复做（或到某个时间点做）的事：建成自动化，不当场派活
SCHEDULE = re.compile(r"每(?:个)?(?:天|日|晚|早|周|星期|礼拜|月|隔|个?工作日)|工作日|周一到周五|天天|定时|定期"
                      r"|(?:明天|后天|下周[一二三四五六日天]?)(?:早上|上午|中午|下午|晚上)?\s*\S{1,3}[:：点时]")
HELLO = re.compile(r"^(你好|在吗|hi|hello|嗨|早上好|晚上好|谢谢|辛苦了|好的|收到|OK|ok)[!！。.～~]*$", re.I)
# 明确叫停 / 取消正在做的事（不含「改成 / 换成 / 改做」等推翻重做表达，不含疑问句）
STOP = re.compile(
    r"^(?:先)?(?:停下|停一下|停止|停掉|别做了|不要做了|不用做了|不做了|别弄了|不用弄了|不用了|取消(?:吧|掉|任务|这个任务)?|终止|中止|歇息|算了(?:吧)?|都停下|全部停下)(?:[，,、\s]*(?:歇息|休息|停下|别做了|不用做了|取消|算了|吧|呀|啦|了))*[!！。.～~]*$"
    r"|^(?:这个|这件|当前|正在做的|刚才那个|那件)?(?:任务|活|事|工作)(?:先)?(?:不用做了|别做了|不要做了|不做了|停下|停掉|停止|取消(?:吧|掉)?|算了吧?)[!！。.～~]*$"
)
STOP_REDIRECT = re.compile(r"改成|换成|改做|改写|重新|重做|重来|换个")


def is_stop(text: str) -> bool:
    t = text.strip()
    if not t or re.search(r"[？?]\s*$|吗[。]?$", t) or STOP_REDIRECT.search(t):
        return False
    return bool(STOP.search(t))


def live_jobs() -> list[dict]:
    return [t for t in db.main_tasks() if t["status"] not in db.TERMINAL]


def _interject_like(text: str, jobs: list[dict]) -> bool:
    if not jobs:
        return False
    if any(w in text for w in classify.OVERTURN + classify.APPEND + classify.AUGMENT):
        return True
    if classify.NEGATE.search(text) or classify.ALSO.search(text):
        return True
    labels = {k["label"] for j in jobs for k in db.children(j["id"])}
    return any(re.search(rf"(?<![A-Za-z]){l}(?![A-Za-z])", text) for l in labels if l)


def pick_job(text: str, jobs: list[dict]) -> dict | None:
    """说的是哪件事：标题、子任务标题的字面重合最多的；都不沾边就取最近开工的。"""
    if not jobs:
        return None
    said = textsim.grams(text)
    best, score = None, 0
    for j in jobs:
        own = textsim.grams(j["title"] + " " + " ".join(k["title"] for k in db.children(j["id"])))
        sc = len(said & own)
        if sc > score:
            best, score = j, sc
    return best or jobs[0]


def rule_route(text: str, jobs: list[dict]) -> dict:
    if "【密钥" in text or SETUP.search(text):
        return {"kind": "chat", "source": "rule"}
    a = recent_automation() if RUN_ONCE.match(text.strip()) else None
    if a:
        return {"kind": "run_auto", "automation": a["id"], "source": "rule"}
    t = text.strip()
    if HELLO.match(t) or memory.explicit(t):
        return {"kind": "chat", "source": "rule"}
    if jobs and is_stop(t):
        return {"kind": "stop", "job": pick_job(t, jobs)["id"], "source": "rule"}
    if SCHEDULE.search(t) and not re.search(r"[？?]\s*$|吗[。]?$", t):
        return {"kind": "schedule", "source": "rule"}
    if jobs and re.search(r"[？?]\s*$|吗[。]?$", t) and not re.search(r"^(能不能|可不可以|帮我|请)", t):
        return {"kind": "chat", "source": "rule"}  # 问问题不当插话
    if _interject_like(t, jobs):
        return {"kind": "interject", "job": pick_job(t, jobs)["id"], "source": "rule"}
    if WORK.search(t) and not (ASK.search(t) and len(t) < 14):
        return {"kind": "new", "source": "rule"}
    if ASK.search(t):
        return {"kind": "chat", "source": "rule"}
    if jobs:
        return {"kind": "interject", "job": pick_job(t, jobs)["id"], "source": "rule"}
    return {"kind": "new" if len(t) >= 8 else "chat", "source": "rule"}


DECIDE = """你是主人的个人助理 {name}。主人刚说了一句话，你要决定怎么处理，调用且只调用一个工具：
- reply：闲聊、提问、随口发个网页/公众号链接问里面写了什么或问你能不能做到、问进度、问文件在哪、问你会什么、问或改你自己的设置（用什么搜索、换模式、接或停连接器、装技能、给队员加技能、主人发来 key）——直接自己读完回答就行，不用派活
- start_job：一件现在就要动手做并交付产物的活（深度调研、写报告、写代码、做网页、做 PPT、整理对照表保存到文件…），和正在做的事不是一回事
- schedule：要你以后到点才做、或按时间反复做的事（每天 / 每个工作日 / 每周几 / 每月几号 / 每隔多久 / 明天几点…）。
  注意：带时间规律的一律用 schedule，不要当场派活；主人说「现在先做一次」时把 run_now 设成 true
- interject：针对下面某件正在做的事插话（加要求、改方向、多做一件相关的事）
  注意：主人是在问问题（以「？」「吗」结尾，比如「不用先跟我对大纲吗？」），哪怕说的是正在做的事，也用 reply 回答，不要当插话
- stop_job：主人明确叫停、取消或放弃正在做的事（「不用了」「别做了」「这个任务不用做了」「停下」「取消」「歇息」），直接停止任务，绝不用 interject 去推翻重做
- run_automation：主人要你现在就跑一次某条已有的自动化（「先跑一次」「现在跑一下看看」通常指最近刚建的那条）
start_job 的 goal 和 schedule 的 text 要写成一句完整、能单独看懂的指令：把「刚才那个」「按上面说的」换成对话里的具体内容，
保留主人原话里的要求，不要自己加新要求。
现在是 {now}。

正在做的事：
{jobs}

已有的自动化（最新的在前）：
{autos}

最近的对话：
{recent}
{earlier}"""

RULE_SCHEMA = {"type": "object", "description": "时间规则，只能是这几种之一："
               "{type:daily,time:HH:MM} / {type:weekdays,time:HH:MM} / {type:weekly,time:HH:MM,days:[1-7，1 是周一]} / "
               "{type:monthly,time:HH:MM,day:1-31} / {type:interval,hours:小时数} / {type:once,at:'YYYY-MM-DD HH:MM'}；没说时间默认 09:00"}


def _fn(name: str, desc: str, props: dict | None = None, req: list | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": req or []}}}


DECIDE_TOOLS = [
    _fn("reply", "直接回答，不派活"),
    _fn("start_job", "现在派队员去做一件新的活", {"goal": {"type": "string", "description": "完整的任务描述"}}, ["goal"]),
    _fn("schedule", "建一条定时自动化，到点把 text 交给自己去做",
        {"name": {"type": "string", "description": "≤12 字的名字"}, "rule": RULE_SCHEMA,
         "text": {"type": "string", "description": "到点要做的事，一句完整的话，不含时间"},
         "run_now": {"type": "boolean", "description": "主人要求现在先做一次"}}, ["rule", "text"]),
    _fn("interject", "对正在做的某件事插话", {"job": {"type": "string", "description": "那件事的 id"}}, ["job"]),
    _fn("stop_job", "停止、取消正在做的某件事", {"job": {"type": "string", "description": "要停下的那件事的 id"}}, []),
    _fn("run_automation", "现在跑一次某条已有的自动化", {"id": {"type": "integer", "description": "自动化的 id"}}, ["id"]),
]

SETUP = re.compile(r"(?:用|换|改|配)(?:的)?什么(?:搜索|检索|模型|模式)|搜索服务|联网检索|(?:接|配|加|停|删)(?:上|个|一下)?[^，。]{0,8}连接器|(?:换|切)(?:成|到)?(?:自动|手动|计划|全部放行)模式|(?:装|安装|导入|加|删|卸)(?:上|个|一下|几个)?[^，。]{0,12}技能|技能库")
RUN_ONCE = re.compile(r"^(?:那)?(?:就)?(?:现在|马上|立刻)?(?:先)?(?:跑|执行|做|试)(?:一次|一下|一遍|一回)(?:看看|吧|试试)?[。！!～~]?$")


def recent_automation() -> dict | None:
    """最近几条消息里刚建的自动化：「先跑一次」默认指它。"""
    from .. import automation
    for m in db.q("SELECT meta FROM messages ORDER BY id DESC LIMIT 6"):
        meta = m["meta"] if isinstance(m["meta"], dict) else json.loads(m["meta"] or "{}")
        if meta.get("automation_id"):
            return automation.get(int(meta["automation_id"]))
    return None


async def model_route(text: str, jobs: list[dict]) -> dict:
    import datetime as dt
    from .. import chatlog
    recent = chatlog.recent(8)  # 清屏后的话；清屏前的前情拼在下面
    if recent and recent[0]["role"] == "user" and recent[0]["content"] == text:
        recent = recent[1:]
    jl = [{"id": j["id"], "title": j["title"], "goal": j["goal"][:120],
           "parts": [k["title"] for k in db.children(j["id"])]} for j in jobs]
    from .. import automation
    autos = [{"id": a["id"], "name": a["name"], "when": a["when"], "text": a["text"][:80]} for a in automation.listing()[:8]]
    sys = DECIDE.format(name=memory.assistant_name(), now=dt.datetime.now().strftime("%Y-%m-%d %H:%M %A"),
                        jobs=json.dumps(jl, ensure_ascii=False) if jl else "（没有）",
                        autos=json.dumps(autos, ensure_ascii=False) if autos else "（没有）",
                        recent="\n".join(f"{m['role']}：{m['content'][:160]}" for m in reversed(recent)) or "（没有）",
                        earlier=chatlog.earlier(2))
    r = await llm.chat_tools([{"role": "system", "content": sys}, {"role": "user", "content": text}],
                             DECIDE_TOOLS, fast=True, timeout=decide_timeout(), temperature=0)
    calls = r.get("tool_calls") or []
    if not calls:
        return {"kind": "chat", "source": "model"}
    name = calls[0]["function"]["name"]
    try:
        args = json.loads(calls[0]["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    if name == "start_job":
        return {"kind": "new", "goal": str(args.get("goal") or "").strip() or None, "source": "model"}
    if name == "schedule":
        return {"kind": "schedule", "auto": args, "source": "model"}
    if name == "stop_job":
        if not jobs:
            return {"kind": "chat", "source": "model"}
        ids = {j["id"] for j in jobs}
        job = args.get("job") if args.get("job") in ids else (pick_job(text, jobs) or {}).get("id")
        return {"kind": "stop", "job": job, "source": "model"}
    if name == "interject":
        ids = {j["id"] for j in jobs}
        if not jobs:
            return {"kind": "new", "source": "model"}
        job = args.get("job") if args.get("job") in ids else (pick_job(text, jobs) or {}).get("id")
        return {"kind": "interject", "job": job, "source": "model"}
    if name == "run_automation":
        from .. import automation
        try:
            a = automation.get(int(args.get("id")))
        except (TypeError, ValueError):
            a = None
        a = a or recent_automation()
        if a:
            return {"kind": "run_auto", "automation": a["id"], "source": "model"}
        return {"kind": "chat", "source": "model"}
    if name == "reply":
        return {"kind": "chat", "source": "model"}
    raise ValueError(name)


def decide_timeout() -> float:
    return max(classify.timeout() * 1.5, 10.0)


async def route(text: str) -> dict:
    jobs = live_jobs()
    if memory.explicit(text) or "【密钥" in text:
        return {"kind": "chat", "source": "rule"}
    if jobs and is_stop(text):
        return {"kind": "stop", "job": pick_job(text, jobs)["id"], "source": "rule"}
    if RUN_ONCE.match(text.strip()) and recent_automation():
        return rule_route(text, jobs)
    if llm.configured():
        try:
            return await asyncio.wait_for(model_route(text, jobs), decide_timeout() + 1)
        except Exception:  # noqa: BLE001 — 超时、断网、非法输出都走规则
            pass
    return rule_route(text, jobs)


# ---------- 闲聊 / 提问 ----------
def _team() -> str:
    lines = []
    for c in registry.active():
        skills = "、".join(k.name for k in c.skills)
        lines.append(f"- {c.name}{f'（{c.team}）' if c.team else ''}：{c.introduction}（会：{skills}）")
    glob = t_skills.global_skills()
    conns = [n for n, cfg in t_mcp.load().items() if cfg.get("enabled", True)]
    out = "你的队员：\n" + ("\n".join(lines) or "（没有）")
    out += "\n每个队员都能联网搜索、读网页、读写工作文件夹里的文件、执行命令。"
    from ..tools import mail as t_mail
    out += "\n邮箱：" + ("已接上，能搜信、读信、起草，发信前先问主人" if t_mail.configured() else "还没配（设置 · 邮箱）")
    if glob:
        out += "\n公共技能：" + "、".join(g.name for g in glob)
    out += "\n接上的连接器：" + ("、".join(conns) if conns else "（还没有）")
    return out


def _context(text: str = "") -> str:
    jobs = db.main_tasks()[:8]
    lines = []
    for j in jobs:
        kids = db.children(j["id"])
        parts = "、".join(f"{k['label']} {k['title']}（{k['status']}）" for k in kids)
        lines.append(f"- {j['title']}：{j['status']}，进度 {round(j['progress'] * 100)}%{'；' + parts if parts else ''}")
    files = db.q("SELECT name, path FROM artifacts ORDER BY created_at DESC LIMIT 20")
    flines = [f"- {f['name']}：{f['path']}" for f in files]
    mem = memory.block(text, 10)
    return (_team() + "\n\n最近的事：\n" + ("\n".join(lines) or "（没有）") + "\n\n产物文件：\n"
            + ("\n".join(flines) or "（没有）") + f"\n\n工作文件夹：{settings.workspace()}"
            + "\n队员、技能、连接器可以在左侧「队员」「技能」「连接器」里新建或接入；设置在左下角。"
            + (f"\n\n{mem}" if mem else ""))


async def _talk(msgs: list[dict]) -> str:
    """聊天时能查、改自己的设置（搜索服务、模式、干活方式）：最多调 4 轮工具，再说话。"""
    from .. import setup
    msgs = list(msgs)
    msgs[0] = {**msgs[0], "content": msgs[0]["content"] + (
        "\n\n主人问起之前说过的事、交过的文件，前情里没有细节时用 search_history 翻聊天记录再答。"
        "主人发了网页或微信公众号链接问里面写了什么、能不能做到时，先用 read_url 打开读完原文再回答，不要凭记忆说打不开。"
        "主人问你自己的设置（用什么搜索、什么模式、接了哪些连接器、有哪些技能），或者要你换设置、接连接器、装技能、给队员加技能、发来 key 时，用工具查和改，自己办好，"
        "不要叫主人去设置页。key 在对话里显示成【密钥 …】：那是打了码的样子，完整的 key 已经收到，原样传给工具就行，别让主人重发完整的；"
        "改完用一两句话说结果，不要复述 key。"
        "主人给你起名、改称呼、要你换个性格或说话方式时，用 set_persona 改人设卡；纠正你说话或做事的习惯时，用 add_style_rule 记下来。"
        + soul.onboarding_hint())}
    for _ in range(4):
        r = await llm.chat_tools(msgs, setup.TOOLS, timeout=60, temperature=0.5, fast=True)  # 聊天用日常模型
        calls = r.get("tool_calls") or []
        if not calls:
            return (r.get("content") or "").strip()
        msgs.append(r)
        for c in calls:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = await setup.call(c["function"]["name"], args)
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
    r = await llm.chat_tools(msgs, [], timeout=60, temperature=0.5, fast=True)
    return (r.get("content") or "").strip()


async def reply(text: str, files: list[dict] | None = None) -> str:
    files = files or []
    if llm.configured():
        from .. import chatlog
        recent = chatlog.recent(12)
        msgs = [{"role": "system", "content": (
            soul.block() + "\n\n## 这一次\n"
            "你手下有一组队员能查资料、写文档、读写文件、跑命令。这次只是聊天或提问，你自己答。"
            "这一步你没有派活、没有建或跑自动化：不要说「这就让 XX 去做」「已安排」「马上开始」这类话；"
            "主人想让你动手，请他直接说要做什么（或点自动化消息下的「现在先跑一次」）。"
            "不知道的别编；问到队员、能力时按下面的实际情况说。\n\n" + _context(text)
            + (f"\n\n主人的偏好：{settings.yip()['note']}" if settings.yip().get("note") else "")
            + (f"\n\n{chatlog.earlier()}" if chatlog.earlier() else ""))}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in reversed(recent)]
        if recent and recent[0]["role"] == "user" and recent[0]["content"] == text:
            msgs.pop()
        block = uploads.text_block(files)
        imgs = [u for u in files if u["kind"] == "image"]
        user_text = text + (f"\n\n{block}" if block else "")
        if imgs:
            content = [{"type": "text", "text": user_text}] + [uploads.image_part(u) for u in imgs]
            # 主模型看不了就让快模型直接看图回答（DeepSeek：v4-pro 不收图，flash 收），都不行再找看图模型
            got = (await uploads.look(msgs + [{"role": "user", "content": content}], daily_first=True,
                                      temperature=0.5)).strip()
            if got:
                return soul.tidy(got)
            return "现在配的模型都看不了图；可以在「设置 · 模型」里配一个看图模型（比如通义 qwen-vl），或者把图里的内容打成字发我"
        msgs.append({"role": "user", "content": user_text})
        asking = bool(soul.onboarding_hint())
        try:
            out = soul.tidy(await _talk(msgs))
            if asking:  # 起名只问一次
                settings.save({"yip": {"soul": {"asked": True}}})
            return out
        except Exception:  # noqa: BLE001
            return "模型这会儿没回应，稍后再问我一次；要干的活可以直接交代，我先按规则派出去"
    if re.search(r"队员|团队|能做什么|会什么", text):
        return _team()
    files = db.q("SELECT name, path FROM artifacts ORDER BY created_at DESC LIMIT 5")
    if files and re.search(r"文件|文档|在哪|放哪", text):
        return "最近的文件：\n" + "\n".join(f"- {f['name']}：{f['path']}" for f in files)
    return "还没配模型，我只能接活不能聊天；说一件要办的事，比如「整理三家竞品的简报，重点看定价和渠道」"
