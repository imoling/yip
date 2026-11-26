"""队员：单个子任务的工具调用循环，独立 compiled graph，thread_id = task:{id}。不感知全局。

配了模型（agent 模式）：

    load_context（读最新约束）→ agent（带工具调一次模型）→ gate（要不要问人）→ tools（执行）→ load_context …
                                    └─ 没有工具调用 / 到轮数上限 → finish

gate 要问人时落 approvals 记录，用 LangGraph interrupt() 停住；人在界面上点完，runner 用 Command(resume=…) 接着跑。
gate 本身没有副作用（记录按 tool_call id 去重），恢复时整节点重跑也安全；执行都在 tools 节点里。

没配模型（demo 模式）：保留原来的「选技能 → 跑演示脚本 → 汇总」，离线也能把插话流程走通。

约束以数据库为准：M1 实验证明运行中 update_state 会被正在跑的 run 覆盖，所以每轮开头从 DB 读约束。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import db, events, llm, memory, settings, team, textsim, verify
from .tools import Ctx, Tool, ToolError
from .tools import ask as t_ask
from .tools import files as t_files
from .tools import mail as t_mail
from .tools import mcp as t_mcp
from .tools import browser as t_browser
from .tools import office as t_office
from .tools import computer as t_computer
from .tools import page as t_page
from .tools import risk as t_risk
from .tools import shell as t_shell
from .tools import skills as t_skills
from .tools import web as t_web
from .workers import registry, skill_exec


class WorkerState(TypedDict, total=False):
    task_id: str
    run_id: str
    worker_id: str
    goal: str
    constraints: list[str]
    seen: int                 # 已经用上的约束条数
    scratchpad: list[dict]    # demo 模式的过程记录
    step: int
    max_steps: int
    result: str | None
    mode: str                 # agent / demo
    messages: list[dict]      # agent 模式的对话（不含 system，system 每轮现拼）
    pending: list[dict]       # 这一轮要执行的 tool_calls
    reworks: int              # 验收没过、退回返工的次数
    verdict: dict | None      # 最近一次验收结果
    team_seen: int            # 小队频道里看到第几条了
    stuck: int                # 连着几轮工具全报错（卡住了）
    pro: bool                 # 已经换成难活模型
    # deep 模式（worker_deep.py）
    inbox: list[str]          # 下次进 Deep Agent 要塞进去的话（返工意见、复查请求）
    wait: list[int]           # 等人放行的 approvals id
    again: bool               # 放行都已经有结果了，直接接着跑
    deep_thread: str          # Deep Agent 对话的 thread 后缀；推翻 / 改派换新的
    deep_secs: float


def step_delay() -> float:
    lo, _, hi = os.environ.get("SA_STEP_DELAY", "2,4").partition(",")
    return random.uniform(float(lo), float(hi or lo))


def _alive(s: WorkerState) -> bool:
    t = db.task(s["task_id"])
    return bool(t and t["run_id"] == s["run_id"])


def _main_of(task_id: str) -> dict:
    t = db.task(task_id) or {}
    return db.task(t["parent_id"]) if t.get("parent_id") else t


def ctx_of(s: WorkerState) -> Ctx:
    main = _main_of(s["task_id"])
    return Ctx(task_id=s["task_id"], main_id=main.get("id", s["task_id"]), worker_id=s["worker_id"],
               workspace=settings.workspace(), constraints=list(s.get("constraints") or []))


async def load_context(s: WorkerState) -> dict:
    t = db.task(s["task_id"]) or {}
    cons = list(t.get("constraints") or [])
    out: dict = {"constraints": cons}
    if s.get("mode") == "agent":
        new = cons[s.get("seen", 0):]
        if new:
            events.event(s["task_id"], "constraint_applied", f"这一轮起按新要求做：{'；'.join(new)}")
            out["messages"] = s["messages"] + [{"role": "user", "content": (
                "【插话补充，从现在起照这个做，和之前冲突的以这里为准】\n" + "\n".join(f"- {c}" for c in new))}]
            out["seen"] = len(cons)
        me = db.task(s["task_id"]) or {}
        if me.get("parent_id"):
            text, last = team.unread(me["parent_id"], s["task_id"], s.get("team_seen") or 0)
            if text:
                out["messages"] = out.get("messages", s["messages"]) + [{"role": "user", "content": text}]
                out["team_seen"] = last
    return out


# ---------- agent 模式 ----------
async def toolset(card, s: WorkerState) -> tuple[list[Tool], list]:
    pref = registry.prefs(card.id)
    skills = t_skills.for_worker(card, pref["skills"])
    tools = t_web.TOOLS + t_files.TOOLS + t_shell.TOOLS + t_ask.TOOLS + t_skills.tools_for(card, skills)
    if verify.browser():
        tools += t_page.TOOLS
    tools += t_office.TOOLS
    if t_browser.available():
        tools += t_browser.TOOLS
    if t_computer.available():
        tools += t_computer.TOOLS
    if t_mail.configured():
        tools += t_mail.TOOLS
    me = db.task(s["task_id"]) or {}
    if me.get("parent_id") and len(db.children(me["parent_id"])) > 1:
        tools += team.TOOLS
    try:
        tools += await t_mcp.tools_for(pref["connectors"])
    except Exception as e:  # noqa: BLE001 — 连接器坏了不挡主流程
        events.event(s["task_id"], "error", f"连接器没连上：{type(e).__name__}")
    return tools, skills


def _relay(me: dict) -> str:
    """前面接力的人交了什么：结果摘要放进提示，文件让队员自己去项目文件夹里看。"""
    out = []
    for pid in me.get("prereqs") or []:
        t = db.task(pid)
        if not t:
            continue
        card = registry.get(t["worker_id"])
        state = {"done": "已交", "failed": "中断了", "cancelled": "取消了"}.get(t["status"], t["status"])
        out.append(f"### {t['label']} {t['title']}（{card.name if card else t['worker_id']}，{state}）\n"
                   f"{(t['result'] or '没有留下说明').strip()[:1500]}")
    return ("## 前面的人交了什么\n" + "\n\n".join(out)) if out else ""


def system_prompt(card, s: WorkerState, skills) -> str:
    y = settings.yip()
    main = _main_of(s["task_id"])
    folder = (main.get("title") or "").replace("/", "-").strip()
    me = db.task(s["task_id"]) or {}
    title = (me.get("title") or "结果").replace("/", "-")
    mine = card.name.replace(" ", "")  # 笔记文件名带队员名：「整理成果-报告撰写员.md」，不用 A、B 代号
    cons = s.get("constraints") or []
    parts = [
        card.prompt[:4000],
        f"## 这次的活（今天是 {verify.today()}）\n{s['goal']}",
        f"## 整件事\n{main.get('goal') or ''}",
        ("## 验收标准（交付后会逐条检查，不过会退回返工）\n" + "\n".join(f"- {a}" for a in me.get("accept") or []))
        if me.get("accept") else "",
        "## 必须遵守的要求（后来的说了算）\n" + ("\n".join(f"- {c}" for c in cons) if cons else "无"),
        _relay(me),
        team.roster(me),
        "## 工作方式\n"
        f"- 工作文件夹是 {settings.workspace()}，文件工具的路径都相对它；这件事的项目文件夹是「{folder}/」，大家都在里面干活\n"
        f"- 开工先 list_dir「{folder}」看看已经有什么；接着前面的人的文件改，不要另起一套\n"
        "- 「附件/」里的文件只有任务里点名了才看\n"
        "- 公开信息先 web_search 再 web_fetch 看原文；只写查到的事实，不编数字，查不到写【待补充】\n"
        "- 报告、对比和文字类文档默认只写 Markdown（.md），不要额外生成 Word；只有主人或所用技能明确要求 PPT / Word / Excel / PDF 时，"
        "才用 make_pptx / make_docx / make_xlsx / make_pdf 直接生成对应格式（给了 Word 等真文件就不用再另写一份同内容的 .md）；"
        "不要自己写脚本；读这类文件用 read_file\n"
        "- web_fetch 读不出来、要点按钮翻页、要登录才能看的网站，用 browser_open 打开，按编号 browser_click / browser_type；"
        "遇到登录页用 browser_login 请主人登录，不要自己猜账号密码；要网页截图用 browser_open 打开再 browser_screenshot，不要自己写脚本开浏览器\n"
        "- 要操作 Mac 上的应用（备忘录、日历、Finder…）：computer_open 打开，computer_look 看界面，按编号 computer_click / computer_type；"
        "每一步都会先问主人，所以步骤要少、要准\n"
        f"- 代码、网页这类能运行的东西直接写进项目文件夹（网页入口是「{folder}/index.html」，双击能打开，不依赖构建和联网）；"
        f"写完要自己检查一遍能不能跑，有命令行就跑一下\n"
        f"- 文字类结果（需求、调研、测试报告、审查意见）写成 Markdown：「{folder}/{title}-{mine}.md」；写完不要整篇复述\n"
        "- 日期：原文只写了月日、或者说「明天」「下周三」，年份和具体哪天按今天的日期推；写星期几前用 run_shell 跑 python3 -c 算一下，不要猜\n"
        f"- 最多 {int(y['max_rounds'])} 轮工具调用；够交付了就停\n"
        "- 遇到关键方向拿不准、有 2 到 5 个方案要主人拍板时，用 ask_user 出选项卡片让主人点一下再继续\n"
        "- 最后一条回复不调工具，用 3 到 6 行说清：做了什么、交了哪些文件、怎么用、还有什么没做好",
    ]
    cat = t_skills.catalog(skills)
    if cat:
        parts.append(cat)
    if y.get("note"):
        parts.append(f"## 主人的偏好\n{y['note']}")
    mem = memory.block(s["goal"], 6)
    if mem:
        parts.append(mem)
    return "\n\n".join(p for p in parts if p)


def need_pro(s: dict) -> str:
    """该不该换难活模型：返回换的理由，不用换返回空串。日常模型打底，这三种情况升级。"""
    if (db.task(s["task_id"]) or {}).get("hard"):
        return "这块活难"
    if s.get("reworks"):
        return "验收没过，返工"
    if (s.get("stuck") or 0) >= 2:
        return "连着出错，卡住了"
    return ""


def escalate(s: dict) -> tuple[bool, dict]:
    """返回（这一轮用不用日常模型，要写回状态的东西）；第一次升级时在动态里说一声。"""
    why = need_pro(s)
    if not why:
        return True, {}
    if not s.get("pro"):
        events.event(s["task_id"], "escalate", f"{why}，换难活模型来做")
    return False, {"pro": True}


async def agent(s: WorkerState) -> dict:
    card = registry.get(s["worker_id"]) or registry.general()
    tools, skills = await toolset(card, s)
    msgs = [{"role": "system", "content": system_prompt(card, s, skills)}] + s["messages"]
    last = s["step"] >= int(settings.yip()["max_rounds"])
    if last:
        msgs.append({"role": "user", "content": "工具轮数用完了，不要再调工具，按现有信息直接交付。"})
    db.update("tasks", s["task_id"], note="正在想下一步")
    events.task_changed(s["task_id"])
    daily, up = escalate(s)
    reply = await llm.chat_tools(msgs, [] if last else [t.schema() for t in tools], timeout=180,  # 写长文件要一两分钟
                                 fast=daily)
    if not _alive(s):
        raise asyncio.CancelledError()
    calls = reply.get("tool_calls") or []
    if reply.get("content") and calls:
        events.event(s["task_id"], "thought", reply["content"][:300])
    out = {"messages": s["messages"] + [reply], "pending": calls, **up}
    if not calls:
        out["result"] = reply.get("content") or ""
    return out


def _args(call: dict) -> dict:
    try:
        a = json.loads(call["function"].get("arguments") or "{}")
        return a if isinstance(a, dict) else {}
    except json.JSONDecodeError:
        return {}


def _approval(s: WorkerState, call: dict) -> dict | None:
    return db.q1("SELECT * FROM approvals WHERE task_id=? AND run_id=? AND json_extract(args, '$.call_id')=?",
                 (s["task_id"], s["run_id"], call["id"]))


async def gate(s: WorkerState) -> dict:
    """要问人的调用落一条 approvals 记录（按 call id 去重），有没定的就停住等。"""
    card = registry.get(s["worker_id"]) or registry.general()
    tools, _ = await toolset(card, s)
    by = {t.name: t for t in tools}
    main = _main_of(s["task_id"])
    policy = settings.yip()["approval"]
    grants = list((db.task(main["id"]) or {}).get("grants") or [])
    waiting = []
    for call in s.get("pending") or []:
        tool = by.get(call["function"]["name"])
        if not tool or t_risk.decide(tool.name, tool.risk, policy, grants, _args(call), str(settings.workspace())) == "allow":
            continue
        row = _approval(s, call)
        if not row:
            args = _args(call)
            aid = db.insert("approvals", task_id=s["task_id"], main_id=main["id"], run_id=s["run_id"],
                            worker_id=s["worker_id"], tool=tool.name, args={"call_id": call["id"], **args},
                            summary=tool.brief(args), risk=tool.risk, status="pending", created_at=db.now())
            row = db.q1("SELECT * FROM approvals WHERE id=?", (aid,))
            events.event(s["task_id"], "approval", f"等你允许：{row['summary']}", approval_id=aid)
            events.publish("approval", row)
        if row["status"] == "pending":
            waiting.append(row)
    if waiting:
        db.update("tasks", s["task_id"], note=f"等你允许：{waiting[0]['summary']}")
        events.task_changed(s["task_id"])
        interrupt({"approvals": [w["id"] for w in waiting]})
    return {}


async def tools_node(s: WorkerState) -> dict:
    card = registry.get(s["worker_id"]) or registry.general()
    tools, _ = await toolset(card, s)
    by = {t.name: t for t in tools}
    ctx = ctx_of(s)
    msgs = list(s["messages"])
    errs = 0
    for call in s.get("pending") or []:
        if not _alive(s):
            raise asyncio.CancelledError()
        name, args = call["function"]["name"], _args(call)
        tool = by.get(name)
        row = _approval(s, call)
        if not tool:
            result = f"没有叫 {name} 的工具"
        elif name == "ask_user" and row:
            if row["status"] == "denied":
                result = "主人跳过了这个问题，按你最稳妥的方案直接继续。"
                events.event(s["task_id"], "step", f"主人跳过选择：{tool.brief(args)}", tool=name, label=tool.label)
            else:
                ans = str(row.get("answer") or "").strip()
                result = f"主人选了：{ans}" if ans else "主人已确认，继续。"
                events.event(s["task_id"], "step", f"主人选了：{ans or '确认'}", tool=name, label=tool.label)
        elif row and row["status"] == "denied":
            result = "主人没有同意这个操作。换个不需要它的办法，或者在交付里说明缺了什么。"
            events.event(s["task_id"], "denied", f"没被允许：{tool.brief(args)}")
        else:
            db.update("tasks", s["task_id"], note=tool.brief(args))
            events.task_changed(s["task_id"])
            try:
                result = await tool.run(ctx, **args)
                events.event(s["task_id"], "step", tool.brief(args), tool=name, label=tool.label, output=result[:600])
            except ToolError as e:
                result = f"出错：{e}"
                errs += 1
                events.event(s["task_id"], "tool_error", f"{tool.brief(args)} 没成功：{e}", tool=name)
            except TypeError as e:
                result = f"参数不对：{e}"
            except Exception as e:  # noqa: BLE001
                errs += 1
                result = f"出错：{type(e).__name__}: {str(e)[:200]}"
                events.event(s["task_id"], "tool_error", f"{tool.brief(args)} 没成功", tool=name, detail=str(e)[:300])
        msgs.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    step = s["step"] + 1
    rounds = int(settings.yip()["max_rounds"])
    db.update("tasks", s["task_id"], progress=round(min(step / max(rounds, 1), 0.95), 3))
    events.task_changed(s["task_id"])
    stuck = (s.get("stuck") or 0) + 1 if s.get("pending") and errs == len(s["pending"]) else 0
    return {"messages": msgs, "pending": [], "step": step, "stuck": stuck}


async def verify_node(s: WorkerState) -> dict:
    """队员说做完了：先验收。不过就把问题塞回对话让它接着改，最多返工 verify.MAX_REWORK 次。"""
    if not _alive(s):
        raise asyncio.CancelledError()
    tid, n = s["task_id"], s.get("reworks") or 0
    deep = s.get("mode") in ("deep", "acp")
    me = db.task(tid) or {}
    if me.get("parent_id") and any(k.get("return_to") == tid and k["status"] not in db.TERMINAL
                                   for k in db.children(me["parent_id"])):
        events.event(tid, "verify_later", "退回的问题还在改，等复查时再验收")
        return {"verdict": None}
    db.update("tasks", tid, note="正在验收")
    events.task_changed(tid)
    # 软件、网页、难活交付前审；其余只做机器检查，模型评审等交给主人之后在后台跑
    now = verify.strict(tid) or me.get("reviewed")
    r = await verify.review(tid, s.get("result") or "", model=bool(now))
    if not _alive(s):
        raise asyncio.CancelledError()
    if r["pass"]:
        if not now:
            r["later"] = True
            events.event(tid, "verified", "机器检查通过，先交，细看在后台")
        else:
            events.event(tid, "verified", "验收通过" + (f"（返工 {n} 次）" if n else ""), score=r.get("score"))
        return {"verdict": r}
    if n >= verify.MAX_REWORK or not r["problems"]:
        events.event(tid, "verify_failed", f"返工 {n} 次还有 {len(r['problems'])} 个问题", problems=r["problems"])
        return {"verdict": r}
    events.event(tid, "rework", f"验收没过，退回返工：{r['problems'][0][:60]}", problems=r["problems"])
    db.update("tasks", tid, note=f"返工第 {n + 1} 次：{r['problems'][0][:40]}")
    events.task_changed(tid)
    rounds = int(settings.yip()["max_rounds"])
    if deep:
        return {"inbox": [verify.feedback(r["problems"], n + 1)], "reworks": n + 1, "verdict": r, "result": None}
    return {"messages": s["messages"] + [{"role": "user", "content": verify.feedback(r["problems"], n + 1)}],
            "reworks": n + 1, "step": min(s["step"], max(0, rounds - 8)), "verdict": r, "result": None}


def route_verify(s: WorkerState) -> str:
    return "load_context" if s.get("result") is None else "finish_agent"


async def finish_agent(s: WorkerState) -> dict:
    card = registry.get(s["worker_id"]) or registry.general()
    if not _alive(s):
        raise asyncio.CancelledError()
    result = (s.get("result") or "").strip() or "- 没有产出"
    v = s.get("verdict") or {}
    ok = v.get("pass", True)
    note = "已完成" if ok else f"交了，还有 {len(v.get('problems') or [])} 个问题"
    me = db.task(s["task_id"]) or {}
    fixing = [k for k in db.children(me["parent_id"]) if k.get("return_to") == s["task_id"]
              and k["status"] not in db.TERMINAL] if me.get("parent_id") else []
    if fixing:
        note = f"等{registry.part_name(fixing[0])}改完复查"
    db.update("tasks", s["task_id"], result=result, progress=1.0, status="done", note=note,
              verdict={"pass": ok, "problems": v.get("problems") or [], "reworks": s.get("reworks") or 0,
                       "score": v.get("score"), **({"later": True} if v.get("later") else {})} if v else None)
    events.event(s["task_id"], "done", f"{card.name} 交付了结果")
    events.task_changed(s["task_id"])
    return {"result": result}


# ---------- demo 模式（没配模型） ----------
def rule_choose(card, s: WorkerState):
    goal = s["goal"]
    focus = goal.split("——", 1)[1] if "——" in goal else goal  # 只看这一块，别被整件事的目标带偏
    text = focus + " " + " ".join(s["constraints"])
    ranked = sorted(card.demos or card.skills, key=lambda k: -textsim.overlap(text, k.match_text))
    return ranked[0], s["goal"]


async def choose(card, s: WorkerState):
    return rule_choose(card, s) + (False,)


async def act(s: WorkerState) -> dict:
    card = registry.get(s["worker_id"]) or registry.general()
    new_cons = s["constraints"][s["seen"]:]
    if new_cons:
        events.event(s["task_id"], "constraint_applied", f"这一轮起按新约束做：{'；'.join(new_cons)}")
    skill, task_input, done = await choose(card, s)
    if done:
        return {"step": s["max_steps"], "seen": len(s["constraints"])}
    db.update("tasks", s["task_id"], note=f"正在{skill.name}")
    events.task_changed(s["task_id"])
    await asyncio.sleep(step_delay())
    out = await skill_exec.run(card, skill, task_input, s["constraints"], s["step"] + 1)
    step = s["step"] + 1
    if not _alive(s):
        raise asyncio.CancelledError()
    db.update("tasks", s["task_id"], progress=round(min(step / s["max_steps"], 0.99), 3))
    events.event(s["task_id"], "step", f"{skill.name}", output=out[:800], step=step,
                 constraints=s["constraints"], worker=card.name)
    events.task_changed(s["task_id"])
    pad = s["scratchpad"] + [{"step": step, "skill": skill.name, "output": out, "constraints": list(s["constraints"])}]
    return {"step": step, "scratchpad": pad, "seen": len(s["constraints"])}


def rule_compose(s: WorkerState) -> str:
    pad = s["scratchpad"]
    cons = s["constraints"]
    if cons:  # 约束变过：只用最新约束下的产出
        latest = [p for p in pad if p["constraints"] == cons]
        pad = latest or pad
    lines, seen = [], set()
    for p in pad:
        for ln in p["output"].splitlines():
            ln = ln.strip()
            if ln and ln not in seen and not ln.startswith("（已按约束"):
                seen.add(ln)
                lines.append(ln if ln.startswith(("-", "|", "#")) else f"- {ln}")
    if cons:
        lines.append(f"\n> 已按约束：{'；'.join(cons)}")
    return "\n".join(lines) or "- 没有产出"


async def finish_demo(s: WorkerState) -> dict:
    card = registry.get(s["worker_id"]) or registry.general()
    result = rule_compose(s)
    if False:  # demo 模式不调模型
        try:
            pad = "\n\n".join(f"[{p['skill']}，约束 {p['constraints']}]\n{p['output']}" for p in s["scratchpad"])
            result = await llm.chat([
                {"role": "system", "content": card.prompt[:4000]},
                {"role": "user", "content": (
                    f"任务：{s['goal']}\n最新约束（必须遵守，和它冲突的旧产出要丢掉）：{s['constraints'] or '无'}\n"
                    f"交付标准：{card.deliverable or '要点清单'}\n\n过程记录：\n{pad}\n\n"
                    "按交付标准整理成 Markdown 正文（不要标题），只用过程记录里的事实，不编数字。"
                )},
            ], json_mode=False, timeout=60)
        except Exception:
            pass
    if not _alive(s):
        raise asyncio.CancelledError()
    db.update("tasks", s["task_id"], result=result, progress=1.0, status="done", note="已完成")
    events.event(s["task_id"], "done", f"{card.name} 交付了结果")
    events.task_changed(s["task_id"])
    return {"result": result}


def route_mode(s: WorkerState) -> str:
    return {"agent": "agent", "deep": "deep", "acp": "acp"}.get(s.get("mode") or "", "act")


def route_demo(s: WorkerState) -> str:
    return "finish_demo" if s["step"] >= s["max_steps"] else "load_context"


def route_agent(s: WorkerState) -> str:
    return "gate" if s.get("pending") else "finish_agent"


def build(checkpointer):
    from . import worker_acp, worker_deep
    worker_deep.SAVER = checkpointer
    g = StateGraph(WorkerState)
    g.add_node("deep", worker_deep.deep)
    g.add_node("hold", worker_deep.hold)
    g.add_node("acp", worker_acp.acp_node)
    g.add_node("load_context", load_context)
    g.add_node("agent", agent)
    g.add_node("gate", gate)
    g.add_node("tools", tools_node)
    g.add_node("verify", verify_node)
    g.add_node("finish_agent", finish_agent)
    g.add_node("act", act)
    g.add_node("finish_demo", finish_demo)
    g.add_edge(START, "load_context")
    g.add_conditional_edges("load_context", route_mode, {"agent": "agent", "act": "act", "deep": "deep", "acp": "acp"})
    g.add_conditional_edges("deep", worker_deep.route_deep, {"hold": "hold", "deep": "deep", "verify": "verify"})
    g.add_edge("hold", "deep")
    g.add_edge("acp", "verify")
    g.add_conditional_edges("agent", route_agent, {"gate": "gate", "finish_agent": "verify"})
    g.add_conditional_edges("verify", route_verify, {"load_context": "load_context", "finish_agent": "finish_agent"})
    g.add_edge("gate", "tools")
    g.add_edge("tools", "load_context")
    g.add_edge("finish_agent", END)
    g.add_conditional_edges("act", route_demo, {"finish_demo": "finish_demo", "load_context": "load_context"})
    g.add_edge("finish_demo", END)
    return g.compile(checkpointer=checkpointer)
