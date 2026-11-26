"""Supervisor：拆解 → 匹配队员 → 派发 → 等全部跑完 → 汇总 → 提炼记忆。只编排，不干活。"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from types import SimpleNamespace

from . import artifacts, db, events, llm, memory, runner, settings, soul, verify
from .workers import match as matcher
from .workers import registry


class SupervisorState(TypedDict):
    main_id: str
    goal: str
    constraints: list[str]
    subtasks: list[dict]
    artifacts: list[str]
    memories: list[dict]
    again: bool               # 后台细看退回改好后再交一版（graph.redeliver）


TITLES = {"定价": "竞品定价策略", "价格": "竞品定价策略", "渠道": "渠道与代理政策", "功能": "产品功能差异",
          "服务": "服务政策", "售后": "服务政策", "客户": "客户案例", "动态": "竞品近期动态"}
DEFAULT_STEPS = 5


def rule_plan(goal: str) -> list[dict]:
    topics = []
    m = re.search(r"(?:重点看|看一下|看看|看下|关注|包括|看)([^。！？!?]+)", goal)
    if m:
        topics = [t.strip(" ，,、") for t in re.split(r"[和及与、,，/]|以及", m.group(1)) if t.strip(" ，,、")]
    # 「这三家供应商的合同条款」→「合同条款」：主语留在整件事的目标里，子任务只写这一块
    topics = [re.sub(r"^(?:这|那|各)?[一二三四五六七八九十几\d]*(?:家|个|份)?[^的，,]{0,6}的", "", t) or t for t in topics]
    topics = [t[:8] for t in topics][:4]
    if not topics:  # 说不出分几块：先收集资料，再起草成文
        topics = ["背景资料", "起草成文"]
    elif "竞品" in goal and not any("功能" in t for t in topics) and len(topics) < 4:
        topics.append("功能")
    if len(topics) < 2:
        topics.append("背景资料")
    out = []
    for t in topics:
        title = next((v for k, v in TITLES.items() if k in t), t)
        out.append({"title": title, "goal": f"{goal.rstrip('。')}——这一块只负责「{t}」", "steps": DEFAULT_STEPS})
    return out[:4]


def _note(text: str = "") -> str:
    n = settings.yip().get("note")
    out = f"\n主人的偏好：{n}" if n else ""
    mem = memory.block(text, 6)
    return out + (f"\n\n{mem}" if mem else "")


PLAN_PROMPT = """把用户的任务拆成 1 到 4 个子任务，交给不同队员。最后的汇总不用拆，系统会做。今天是 {today}。
- 互不依赖的子任务同时开工；要用到前面结果的，用 after 写上它前面那几个子任务的序号（从 0 数），等它们交了再开工
- 做软件、网页、小游戏、脚本这类要交付能运行的东西：按接力拆，后一步 after 前一步。
  单页小游戏、小工具、单个脚本这种小东西只拆两步：「写出完整可运行的版本」→「测试并修好问题」；
  多页面、多模块的项目才拆成「需求和验收标准」→「写出完整可运行的版本」→「测试并修好问题」→「代码审查」，审查只列问题不改代码，验收标准写「问题按严重程度列清、指到文件和位置」；
  「写出完整可运行的版本」只交给一个人做完，不要把界面、逻辑、音效拆给不同的人；网页类入口统一是 index.html，能直接双击打开，不依赖构建工具和联网
- 所有人在同一个项目文件夹里干活，后面的人能看到前面的人写的文件
- 简单的活拆 1 个就行，不要为了拆而拆：写一份通知、一封信、一页文档这种只拆 1 个，交付前队员自己会检查
- 查资料、问情况（某个球队的战绩、某家公司的近况、某个政策是什么）：交给 1 个人查完写成一份报告；
  只有几块内容多、互不相干、各要单独查很久时才拆成同时做的几块
- 不要单独拆「汇总成文档」「整理成报告」这一步：最后的汇总系统会做
- 除了写软件，不要单独拆「检查 / 核实 / 验证」这一步：队员交付前自己会检查，系统还会再验收一遍
- 报告和文字整理默认交付 Markdown（.md），不要主动要求生成 Word；只有主人或技能明确要 Word / Excel / PPT / PDF 时，交付物才是那个格式的真文件（.docx / .xlsx / .pptx / .pdf），goal 和验收标准里写明文件名和格式；
  写内容和生成文件交给同一个人一步做完（队员有现成的生成工具），不要拆成「写稿」→「做成文件」两步，也不要同时交 Word 和 Markdown 两份
- 主人用中文：处理文本、做界面的默认要支持中文（比如统计词频要能切中文），这类隐含要求写进验收标准
再给这件事起个名字（≤12 字，名词短语，像文件名，比如「DeepSeek 与 Kimi 定价对比」）。
每块标一下难不难（hard）：多文件的软件、要多方核对的调研、复杂的数据分析写 true，会换更强的模型做；
写一份通知、查一个事实、单页小工具这类写 false。
每块写 2 到 4 条验收标准（accept）：交付后会逐条检查，不过就退回返工。要具体、能核对，比如「index.html 双击能打开，方向键能控制蛇移动」
「打开时不报错、不是一上来就结束」「每家的价格都给出处链接」「只列需要回复或决定的邮件，每封写清谁、什么事、截止时间」；不要写「质量高」「内容完整」这种查不了的话；
也不要写队员做不到的（比如「在真浏览器里手动玩过」）：队员能读写文件、跑命令、用无头浏览器打开网页并跑脚本模拟按键点击。
只输出 JSON：{{"title": "这件事的名字", "subtasks": [{{"title": "≤8 字", "goal": "一句话，说清这块要查 / 做什么、交什么", "steps": 3 到 6 的整数, "after": [前面子任务的序号，没有就是空数组], "hard": true/false, "accept": ["验收标准"]}}]}}
任务：{goal}
约束：{constraints}"""


def _after(x: dict, i: int) -> list[int]:
    """只认指向前面的序号：防环、防自指。"""
    raw = x.get("after") or []
    if not isinstance(raw, list):
        raw = [raw]
    out = []
    for v in raw:
        try:
            j = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= j < i and j not in out:
            out.append(j)
    return out


async def _plan_call(s: SupervisorState, *, fast: bool) -> dict:
    return await asyncio.wait_for(llm.chat_json([{"role": "user", "content": PLAN_PROMPT.format(
        today=verify.today(), goal=s["goal"], constraints=s["constraints"] or "无") + _note(s["goal"])}], fast=fast, timeout=45), 45)


async def plan(s: SupervisorState) -> dict:
    main = db.task(s["main_id"]) or {}
    if main.get("worker_id"):
        title = (main.get("title") or s["goal"])[:12].strip() or "指定任务"
        subs = [{"title": title, "goal": s["goal"], "steps": DEFAULT_STEPS, "after": [], "hard": False, "accept": []}]
        events.event(s["main_id"], "planned", "按指定队员直接执行")
        return {"subtasks": subs}
    subs = None
    if llm.configured():
        try:
            r = await _plan_call(s, fast=True)  # 快模型拆活 5 秒左右，主模型要 25 秒；快模型出错再用主模型
            if not [x for x in r.get("subtasks", []) if x.get("title") and x.get("goal")]:
                raise ValueError("没拆出来")
        except Exception:  # noqa: BLE001
            r = None
        try:
            r = r or await _plan_call(s, fast=False)
            raw = [x for x in r.get("subtasks", []) if x.get("title") and x.get("goal")][:4]
            subs = [{"title": str(x["title"])[:12], "goal": str(x["goal"]),
                     "steps": max(3, min(int(x.get("steps", DEFAULT_STEPS)), 6)), "after": _after(x, i),
                     "hard": bool(x.get("hard")),
                     "accept": [str(a)[:120] for a in (x.get("accept") or []) if str(a).strip()][:5]}
                    for i, x in enumerate(raw)]
            if not subs:
                subs = None
            name = str(r.get("title") or "").strip().strip("「」\"'")[:16].strip()  # 截断后再去空格，免得文件夹名尾巴带空格
            if subs and name:
                db.update("tasks", s["main_id"], title=name)
                events.task_changed(s["main_id"])
        except Exception:
            subs = None
    if subs is None:
        subs = rule_plan(s["goal"])
    events.event(s["main_id"], "planned", f"拆成 {len(subs)} 个子任务")
    return {"subtasks": subs}


async def match(s: SupervisorState) -> dict:
    main = db.task(s["main_id"]) or {}
    if main.get("worker_id") and registry.get(main["worker_id"]):
        wid = main["worker_id"]
        return {"subtasks": [{**st, "worker_id": wid, "reason": "你指定的"} for st in s["subtasks"]]}
    res = await matcher.match(s["subtasks"])
    return {"subtasks": [{**st, **m} for st, m in zip(s["subtasks"], res)]}


def human_plan(lines: list[dict]) -> str:
    """派活时说的那句话：像人一样，不报「分几步」「几个子任务」。同时做的说「分头」，接力的说「接着」。"""
    if len(lines) == 1:
        return f"好，交给{lines[0]['worker']}，弄好叫你"
    pos = {x["label"]: i for i, x in enumerate(lines)}
    level: list[int] = []
    for x in lines:
        level.append(1 + max((level[pos[a]] for a in x["after"] if a in pos and pos[a] < len(level)), default=-1))
    if max(level) == 0:  # 全部同时做
        by: dict[str, list[str]] = {}
        for x in lines:
            by.setdefault(x["worker"], []).append(x["title"])
        parts = "，".join(f"{w}负责{'和'.join(ts) if len(ts) == 2 else '、'.join(ts)}" for w, ts in by.items())
        return f"好，分头做：{parts}，做完我汇总给你"

    def say(xs: list[dict]) -> tuple[str, str]:
        """一层里的人和活：（谁，做什么）。同一个人做几样就合起来，几个人同时做就说分头。"""
        ws = list(dict.fromkeys(x["worker"] for x in xs))
        if len(ws) == 1:
            ts = [x["title"] for x in xs]
            return ws[0], ts[0] if len(ts) == 1 else "做" + "和".join(ts)
        return "、".join(ws), "分头" + "和".join(x["title"] for x in xs)

    out, prev, top = [], "", max(level)
    for lv in range(top + 1):
        who, what = say([x for x, l in zip(lines, level) if l == lv])
        same = who == prev
        if lv == 0:
            out.append(f"让{who}先{what}")
        elif lv == top and top >= 2:
            out.append(f"最后{'再' if same else who + '来'}{what}")
        else:
            out.append(f"{'再' if same else who + '接着'}{what}")
        prev = who
    return "好，" + "，".join(out) + "，弄好叫你"


LONG_DOC = re.compile(r"(?i)ppt|pptx|幻灯片|演示文稿|课件|汇报材料|路演|方案|报告|讲稿|演讲稿|简历|白皮书|手册|论文|策划|文章|公众号")
SHORT_DOC = re.compile(r"(?i)通知|邮件|便条|请假|回复|一句话|标题|图表|看板|仪表盘|dashboard|纪要|周报|日报|月报"
                       r"|开头|结尾|一段|几段|[一二两三四五]段|段话|摘要|简介")
OUTLINE = """主人要你做：{goal}
要求：{cons}
先列这份东西的大纲给主人确认，主人点头才开工。格式：
第一行「按这些来：」写你的假设（用在什么场合、给谁看、多长 / 几页、什么语言、风格），一句话说完；
篇幅按常见用法定：主人没说页数时，PPT 8 到 12 页，文档 4 到 6 节，宁短勿长；
然后按页（PPT）或按节（文档）编号列出来，每条一句话写这一页 / 这一节讲什么。
不写客套，不写别的。"""


def needs_outline(goal: str) -> bool:
    """做 PPT、方案、报告这类篇幅长的东西：先跟主人对大纲。短通知、邮件不用。"""
    return bool(LONG_DOC.search(goal)) and not SHORT_DOC.search(goal)


async def draft_outline(main: dict) -> str:
    try:
        return (await llm.chat([{"role": "user", "content": OUTLINE.format(
            goal=main["goal"], cons="；".join(main.get("constraints") or []) or "无")}], fast=True, json_mode=False,
            timeout=40)).strip()
    except Exception:  # noqa: BLE001
        return ""


async def confirm(s: SupervisorState) -> dict:
    """开工前先给主人看：计划模式（或 /计划）看怎么分工；做 PPT、方案这类长文档看大纲。说开工才派人，改了就带着改的话重来。"""
    mid = s["main_id"]
    main = db.task(mid) or {}
    if main.get("plan_ok"):
        return {}
    doc = llm.configured() and needs_outline(main.get("goal") or "")
    if not doc and not (main.get("plan_first") or settings.yip()["approval"] == "plan"):
        return {}
    if doc and main.get("note") != "等你点头":
        outline = await draft_outline(main)
        if outline:
            db.update("tasks", mid, outline=outline, note="等你点头")
            events.message(mid, "assistant", outline + "\n\n说「开工」就按这个做；想改直接说，比如「改成 8 页」「加一页项目经历」",
                           kind="chat", plan_ask=True)
            events.task_changed(mid)
            interrupt({"outline": mid})
            return {}
        if not (main.get("plan_first") or settings.yip()["approval"] == "plan"):
            return {}
    if main.get("note") != "等你点头":
        rows = []
        for i, st in enumerate(s["subtasks"]):
            card = registry.get(st["worker_id"])
            after = "、".join(str(j + 1) for j in st.get("after") or [])
            rows.append(f"{i + 1}. {card.name if card else st['worker_id']}：{st['title']}" + (f"（等 {after} 做完）" if after else ""))
        events.message(mid, "assistant", "打算这样做：\n\n" + "\n".join(rows) + "\n\n说「开工」就派人，想改哪里直接说",
                       kind="chat", plan_ask=True)
        db.update("tasks", mid, note="等你点头")
        events.task_changed(mid)
    interrupt({"plan": mid})
    return {}


async def dispatch(s: SupervisorState) -> dict:
    mid = s["main_id"]
    lines, ids = [], []
    outline = (db.task(mid) or {}).get("outline") or ""
    for st in s["subtasks"]:
        tid = db.new_id()
        label = db.next_label(mid)
        pre = [ids[j] for j in st.get("after") or [] if j < len(ids)]
        db.insert("tasks", id=tid, parent_id=mid, label=label, title=st["title"], goal=st["goal"],
                  constraints=list(s["constraints"]) + ([f"按主人确认过的大纲做，页数、顺序、每页讲什么以大纲为准"
                                                         f"（和验收标准冲突时以大纲为准）：\n{outline}"] if outline else []),
                  status="waiting" if pre else "pending",
                  steps=st.get("steps", DEFAULT_STEPS), prereqs=pre,
                  accept=(["页数、顺序和每页内容跟主人确认过的大纲一致"] if outline else [])
                  + [a for a in st.get("accept") or [] if not (outline and re.search(r"\d+\s*页|页数", a))],
                  hard=int(bool(st.get("hard"))),
                  note=("等" + "、".join(lines[j]["worker"] for j in st["after"] if j < len(lines)) + "交了再做") if pre else None,
                  worker_id=st["worker_id"], assign_reason=st["reason"], thread_id=f"task:{tid}",
                  created_at=db.now(), updated_at=db.now())
        card = registry.get(st["worker_id"])
        lines.append({"label": label, "title": st["title"], "worker": card.name if card else st["worker_id"],
                      "reason": st["reason"], "after": [lines[j]["label"] for j in st.get("after") or [] if j < len(lines)]})
        ids.append(tid)
    db.update("tasks", mid, status="running", note="执行中")
    events.event(mid, "dispatched", f"派发 {len(ids)} 个子任务")
    events.message(mid, "assistant", human_plan(lines), kind="plan", items=lines)
    r = runner.get()
    for tid, st in zip(ids, s["subtasks"]):
        if not st.get("after"):
            await r.start(tid)
    events.task_changed(mid)
    return {"subtasks": [{**st, "id": i} for st, i in zip(s["subtasks"], ids)]}


def _all_done(mid: str) -> bool:
    kids = db.children(mid)
    pending_q = db.q1("SELECT COUNT(*) AS n FROM task_queue WHERE consumed=0 AND (task_id=? OR task_id IN "
                      "(SELECT id FROM tasks WHERE parent_id=?))", (mid, mid))["n"]
    return bool(kids) and all(k["status"] in db.TERMINAL for k in kids) and pending_q == 0


def collect(s: SupervisorState) -> dict:
    """等所有子任务结束。子任务跑完由 runner 主动唤醒，不轮询。"""
    while not _all_done(s["main_id"]):
        interrupt({"waiting": s["main_id"]})
    return {}


GOAL_CHECK = """你在交付前做最后一道检查：主人要的东西，这次交的能不能满足。只按事实判断，不替队员圆。今天是 {today}。
主人的原话：{goal}
必须遵守的要求：{cons}
各块的情况（「验收通过」已经包含机器检查：文件都在、网页用无头浏览器打开过且没报错、脚本语法正确，以及按验收标准逐条核对）：
{kids}
项目文件夹里的文件：{files}
汇总文档开头：
{summary}
只输出 JSON：{{"met": true/false, "gaps": ["还差什么，一句一条，主人看得懂；满足就是空数组"]}}"""


async def goal_check(main: dict, kids: list[dict], files: list[str], summary: str) -> list[str]:
    """对照主人最初的话查一遍：子任务没交的、验收没过的、整体没满足的，都列出来，不假装做完。"""
    hard = []
    for k in kids:
        v = k.get("verdict") or {}
        if k["status"] == "failed":
            hard.append(f"「{k['title']}」没做完")
        for p in (v.get("problems") or [])[:2] if not v.get("pass", True) else []:
            hard.append(f"「{k['title']}」：{p}")
    if not llm.configured():
        return hard[:6]
    lines = []
    for k in kids:
        v = k.get("verdict") or {}
        state = {"done": "已交", "failed": "中断", "cancelled": "取消"}.get(k["status"], k["status"])
        verdict = "" if not v else ("验收通过" if v.get("pass") else f"验收没过：{'；'.join(v.get('problems') or [])[:300]}")
        lines.append(f"- {k['title']}（{state}）{verdict}")
    try:
        r = await llm.chat_json([{"role": "user", "content": GOAL_CHECK.format(
            today=verify.today(), goal=main["goal"][:2000], cons="；".join(main["constraints"] or []) or "无", kids="\n".join(lines),
            files="、".join(files) or "无", summary=summary[:2500])}], fast=True, timeout=30)
        model = [str(g) for g in r.get("gaps") or [] if str(g).strip()] if not r.get("met", True) else []
    except Exception:  # noqa: BLE001
        model = []
    out = hard + [g for g in model if g not in hard]
    return out[:6]


async def _merge(main: dict, cons: list, files: list[str], body: str) -> str:
    """几块结果合成交付文档：快模型写（半分钟内），出错再用主模型。"""
    prompt = (f"把下面几块子任务的结果合成一份交付文档（Markdown）。先结论后论据，结论三句话以内；"
              f"交付的是软件、网页、脚本时，开头先写怎么用（打开哪个文件、跑什么命令）和还有哪些已知问题；"
              f"事实和数字从各块交的文件里原样取用（带上出处链接），文件里有的不要写成【xx】；文件里也没有的才写【待补充】。"
              f"{_note(main['goal'])}\n任务：{main['goal']}\n约束：{cons or '无'}\n"
              f"项目文件夹里的文件：{'、'.join(files) or '无'}\n\n{body}")
    try:
        return await llm.chat([{"role": "user", "content": prompt}], fast=True, json_mode=False, timeout=60)
    except Exception:  # noqa: BLE001
        return await llm.chat([{"role": "user", "content": prompt}], json_mode=False, timeout=90)


OFFICE = {".docx", ".xlsx", ".pptx", ".pdf"}
SCRIPT = {".py", ".sh", ".js", ".mjs"}


def deliverables(made: list[dict], goal: str) -> list[dict]:
    """交付清单：点名要的格式排最前，其次 Office / PDF、网页入口、文档；交了 Office 文件时，生成它的脚本不算交付物。"""
    want = [suf for suf, _ in verify.wanted_formats(goal)]
    has_office = any(Path(a["path"]).suffix.lower() in OFFICE for a in made)

    def rank(a: dict) -> int:
        suf = Path(a["path"]).suffix.lower()
        return (0 if suf in want else 1 if suf in OFFICE else 2 if a["name"].lower() == "index.html"
                else 3 if suf in (".md", ".csv", ".txt", ".html") else 4)
    out = [a for a in made if not (has_office and Path(a["path"]).suffix.lower() in SCRIPT)]
    return sorted(out, key=rank)


async def summarize(s: SupervisorState) -> dict:
    mid = s["main_id"]
    main = db.task(mid)
    kids = [k for k in db.children(mid) if k["status"] == "done"]
    cons = list(main["constraints"] or [])
    sections, left = [], 30000
    for k in kids:
        card = registry.get(k["worker_id"])
        docs = []
        for a in db.q("SELECT path, name FROM artifacts WHERE task_id=? ORDER BY created_at", (k["id"],)):
            f = Path(a["path"])
            if f.suffix.lower() in (".md", ".txt", ".csv") and f.exists() and left > 0:
                t = f.read_text("utf-8", errors="replace")[:min(left, 10000)]
                left -= len(t)
                docs.append(f"### 文件 {a['name']}\n{t}")
        sections.append(f"## {k['title']}\n\n{k['result'] or '- 没有产出'}\n\n"
                        + ("\n\n".join(docs) + "\n\n" if docs else "")
                        + f"<sub>{card.name if card else k['worker_id']}</sub>")
    body = "\n\n".join(sections)
    folder = settings.workspace() / (main["title"].replace("/", "-").strip() or mid)
    live = {mid} | {k["id"] for k in kids}  # 推翻前那一轮的队员已经删了，他们写的文件不算这次的交付
    made = [a for a in artifacts.listing(mid) if Path(a["path"]).exists() and a["task_id"] in live]
    files = sorted({str(Path(a["path"]).relative_to(folder)) if Path(a["path"]).is_relative_to(folder) else a["name"]
                    for a in made})
    paths = [Path(a["path"]) for a in made]
    deliver = deliverables(made, main["goal"])
    # 交给主人的只有：一份报告 + 能直接用的东西（网页、代码、Office、表格）；各人的笔记、中间稿不给，在「产物」里还能找到
    others = dedupe([a for a in deliver if Path(a["path"]).suffix.lower() not in DOCS], folder)
    all_kids = db.children(mid)
    sinks = [k for k in kids if not any(k["id"] in (x.get("prereqs") or []) for x in all_kids)]
    report = main_doc([a for a in made if a["task_id"] == sinks[0]["id"]]) if len(sinks) == 1 else None
    has_doc_file = any(Path(a["path"]).suffix.lower() in {".docx", ".pdf"} for a in others)
    if report and (has_doc_file or (others and artifacts.is_note(report["name"]))):
        report = None  # 已交 Word/PDF 成品时不再同时附 .md 报告；交图片、网页等时队员自己的笔记也不列给主人
    if report or others:  # 有报告、或者交的是能直接用的东西：直接交付，不再让模型另写一份汇总文档
        # 最后一个人已经写好了报告（或者交的是能直接用的东西）：不再让模型重写一遍汇总（要一分钟）
        text = f"# {main['title']}\n\n" + "\n\n".join(f"## {k['title']}\n{k['result'] or ''}" for k in kids)
        report_text = Path(report["path"]).read_text("utf-8", errors="replace") if report else text
        gaps = await goal_check(main, all_kids, files, report_text)
        gaps += [g for g in verify.format_gaps(main["goal"], paths) if g not in gaps]
        if gaps:
            text += "\n\n## 还差什么\n\n" + "\n".join(f"- {g}" for g in gaps) + "\n"
        top = report or (others[0] if others else None)
        db.update("tasks", mid, artifact=top["path"] if top else None, result=text, progress=1.0,
                  note=f"交了，还差 {len(gaps)} 处" if gaps else "已交付", verdict={"pass": not gaps, "problems": gaps})
        events.event(mid, "summarized", "直接交付")
        aid, give = (report["id"] if report else None), others[:5]
    else:
        text = f"# {main['title']}\n\n" + body
        report_text = text
        if llm.configured() and kids:
            try:
                text = await _merge(main, cons, files, body)
                if not text.lstrip().startswith("#"):
                    text = f"# {main['title']}\n\n{text}"
                report_text = text
            except Exception:
                pass
        gaps = await goal_check(main, all_kids, files, text)
        gaps += [g for g in verify.format_gaps(main["goal"], paths) if g not in gaps]
        if gaps:
            text += "\n\n## 还差什么\n\n" + "\n".join(f"- {g}" for g in gaps) + "\n"
        if cons:
            text += f"\n\n---\n已按约束：{'；'.join(cons)}\n"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{main['title'].replace('/', '-').strip()}.md"
        path.write_text(text, "utf-8")
        db.update("tasks", mid, artifact=str(path), result=text, progress=1.0,
                  note=f"交了，还差 {len(gaps)} 处" if gaps else "已交付", verdict={"pass": not gaps, "problems": gaps})
        aid = artifacts.record(SimpleNamespace(task_id=mid, main_id=mid, worker_id=None), path, announce=False)
        events.event(mid, "summarized", "汇总成文档")
        give = others[:5]
    entry = next((a for a in give if a["name"].lower() == "index.html"), None)
    say = await brief(main, report_text, gaps, entry is not None, bool(aid))
    if not say:  # 快模型没回：按模板说
        if gaps:
            say = f"做完了，但还差 {len(gaps)} 处：" + "；".join(gaps[:2]) + ("；详细的在报告里" if aid else "")
        elif entry:
            say = "做完了，打开 index.html 就能用"
        elif aid:
            say = "做完了，详细的在报告里"
        else:
            say = "做完了"
    if s.get("again"):
        say = "改好了，" + say
    events.message(mid, "assistant", say, kind="file", final=True, files=[a["id"] for a in give],
                   **({"artifact_id": aid} if aid else {}))
    db.update("tasks", mid, status="done")  # 交付那句话说完才算做完
    for d in db.q("SELECT id FROM decisions WHERE task_id=? AND status='pending_confirm'", (mid,)):
        db.update("decisions", d["id"], status="cancelled")
        events.publish("decision", {"id": d["id"], "task_id": mid, "status": "cancelled"})
    if main.get("goal_id"):
        from . import goals as _goals
        _goals.record_progress(main["goal_id"], say, task_id=mid)
    events.task_changed(mid)
    if any((k.get("verdict") or {}).get("later") and not k.get("reviewed") for k in all_kids):
        asyncio.create_task(post_review(mid))  # 先交了：模型评审在后台跑
    return {"artifacts": [a["path"] for a in give] + ([report["path"]] if report else [])}


async def post_review(mid: str) -> None:
    """先交后审：交给主人之后，再让难活模型细看一遍。有问题就说一声、退回队员改，改好再交一版；没问题不出声。"""
    for k in db.children(mid):
        v = k.get("verdict") or {}
        if not v.get("later") or k.get("reviewed") or k["status"] != "done":
            continue
        db.update("tasks", k["id"], reviewed=1)
        try:
            r = await verify.review(k["id"], k.get("result") or "")
        except Exception:  # noqa: BLE001 — 后台评审出错不打扰主人
            continue
        if r["pass"] or not r["problems"]:
            events.event(k["id"], "verified", "后台细看也没问题", score=r.get("score"))
            continue
        probs = r["problems"]
        card = registry.get(k["worker_id"])
        events.event(k["id"], "rework", f"后台细看发现问题，退回返工：{probs[0][:60]}", problems=probs)
        events.message(mid, "assistant", f"刚才交的「{k['title']}」细看下来有 {len(probs)} 处问题：{probs[0][:80]}"
                       + ("等" if len(probs) > 1 else "") + f"。我让{card.name if card else '队员'}改，改好再给你")
        db.update("tasks", mid, status="running", note="在改刚才交的东西")
        db.update("tasks", k["id"], redeliver=1)
        events.task_changed(mid)
        await runner.get().reopen(k["id"], verify.feedback(probs, 1), note=f"细看发现问题：{probs[0][:40]}")


async def redeliver(mid: str) -> None:
    """后台评审退回的活改好了：再交一版。"""
    await summarize({"main_id": mid, "goal": "", "constraints": [], "subtasks": [], "artifacts": [], "memories": [],
                     "again": True})


DOCS = {".md", ".txt"}


def dedupe(arts: list[dict], folder: Path) -> list[dict]:
    """同名同大小的文件（队员在两个文件夹里各放了一份）只留一份，优先留这件事的项目文件夹里的。"""
    seen: dict[tuple, dict] = {}
    for a in arts:
        key = (a["name"], a.get("size"))
        cur = seen.get(key)
        if cur is None or (Path(a["path"]).parent == folder and Path(cur["path"]).parent != folder):
            seen[key] = a
    return [a for a in arts if seen.get((a["name"], a.get("size"))) is a]
SCRATCH = re.compile(r"(?i)note|notes|笔记|草稿|draft|raw|原始|临时|tmp|temp|scratch|data|log|缓存|素材")


def main_doc(arts: list[dict]) -> dict | None:
    """最后那个人交的文件里，哪份是给主人看的报告：不要笔记、中间稿；有正式名字的优先于队员笔记（末尾带队员名或老的「A-」开头），同类里挑最长的。"""
    docs = [a for a in arts if Path(a["path"]).suffix.lower() in DOCS and Path(a["path"]).exists()
            and not SCRATCH.search(Path(a["path"]).stem)]
    if not docs:
        return None
    return sorted(docs, key=lambda a: (artifacts.is_note(a["name"]), -(a.get("size") or 0)))[0]


BRIEF = """今天是 {today}。主人说：{goal}
{voice}
下面是队员做出来的结果。用一两句话、最多三句直接回答主人：先给答案，带上关键数字或事实，不用标题、列表和 Markdown。
{extra}
只用结果里写了的事实，不编。
结果：
{report}"""


async def brief(main: dict, report: str, gaps: list[str], app: bool, has_report: bool) -> str:
    """交付那句话：先简单回答，再说详细的在哪。快模型写，最多等 25 秒。"""
    if not llm.configured():
        return ""
    extra = []
    if app:
        extra.append("交付的是能直接用的东西（网页 / 程序）：说清怎么用，比如打开哪个文件、怎么操作")
    if gaps:
        extra.append("最后用一句话说还差什么：" + "；".join(gaps[:3]))
    if has_report:
        extra.append("结尾加一句「详细的在报告里」")
    try:
        out = await llm.chat([{"role": "user", "content": BRIEF.format(
            today=verify.today(), goal=main["goal"][:600], extra="\n".join(extra), report=report[:6000],
            voice=soul.voice())}], fast=True, json_mode=False, timeout=25)
    except Exception:  # noqa: BLE001
        return ""
    out = re.sub(r"[#*`>]+", "", out or "").strip()
    return soul.tidy(out)[:400]


MEM_PROMPT = """从这次任务里提炼最多 3 条以后还用得上的记忆，只要这几类：
- conclusion：定了什么、为什么
- state：做到哪一步、卡在谁那儿
- lesson：哪条路试过不行、为什么（比如哪个网站打不开、哪种搜法有效）
- preference：从主人的插话里看出来的长期偏好（比如「价格统一换算成人民币」）
过程性的往返不要记；没有就少写。只输出 JSON：{{"memories": [{{"kind": "conclusion|state|lesson|preference", "content": "一句话"}}]}}
任务：{goal}
插话记录：{decisions}
交付摘要：{summary}"""


def rule_memories(mid: str) -> list[dict]:
    main = db.task(mid)
    kids = db.children(mid)
    decs = db.q("SELECT * FROM decisions WHERE task_id=? ORDER BY id", (mid,))
    cn = {"augment": "补充", "append": "追加", "overturn": "推翻"}
    path = main["artifact"] or ""
    try:
        path = str(Path(path).relative_to(db.ROOT))
    except ValueError:
        pass
    mems = [{"kind": "conclusion", "content": f"「{main['title']}」已交付，文档在 {path}"}]
    done = sum(k["status"] == "done" for k in kids)
    cnt = {i: sum((d["final_action"] or d["intent"]) == i for d in decs) for i in ("augment", "append", "overturn")}
    mems.append({"kind": "state", "content": f"{done} 个子任务完成；插话 {len(decs)} 次（补充 {cnt['augment']}、追加 {cnt['append']}、推翻 {cnt['overturn']}）"})
    over = [d for d in decs if d["overridden"]]
    if over:
        d = over[-1]
        mems.append({"kind": "lesson", "content": f"「{d['utterance']}」被判成{cn[d['intent']]}，人改成了{cn[d['final_action']]}，同类说法下次按{cn[d['final_action']]}判"})
    return mems[:3]


async def memorize(s: SupervisorState) -> dict:
    mid = s["main_id"]
    mems = rule_memories(mid)
    if llm.configured():
        try:
            main = db.task(mid)
            decs = db.q("SELECT utterance, intent, final_action, overridden FROM decisions WHERE task_id=?", (mid,))
            r = await llm.chat_json(fast=True, messages=[{"role": "user", "content": MEM_PROMPT.format(
                goal=main["goal"], decisions=json.dumps(decs, ensure_ascii=False), summary=(main["result"] or "")[:3000])}],
                timeout=30)
            got = [m for m in r.get("memories", []) if m.get("kind") in ("conclusion", "state", "lesson", "preference") and m.get("content")]
            if got:
                mems = got[:3]
        except Exception:
            pass
    for m in mems:
        memory.add(m["kind"], m["content"], source="job", task_id=mid, worker_id=m.get("worker_id"), confirmed=0)
    events.event(mid, "memorized", f"沉淀 {len(mems)} 条记忆")
    events.task_changed(mid)
    return {"memories": mems}


def build(checkpointer):
    g = StateGraph(SupervisorState)
    for name, fn in [("plan", plan), ("match", match), ("confirm", confirm), ("dispatch", dispatch), ("collect", collect),
                     ("summarize", summarize), ("memorize", memorize)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "match")
    g.add_edge("match", "confirm")
    g.add_edge("confirm", "dispatch")
    g.add_edge("dispatch", "collect")
    g.add_edge("collect", "summarize")
    g.add_edge("summarize", "memorize")
    g.add_edge("memorize", END)
    return g.compile(checkpointer=checkpointer)
