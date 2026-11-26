"""队员的 Deep Agents 引擎（试验）：干活的那一层交给 LangChain Deep Agents，外面照旧是 Yip 的验收、返工、小队和放行。

    load_context → deep（跑一段 Deep Agent）→ 要人放行：hold（interrupt 停住）→ deep 接着跑
                                           └ 说做完了：verify → 不过：把问题塞进 inbox 再回 deep / 过了：finish_agent

Deep Agent 自己带的：待办清单（write_todos）、文件工具（ls / read_file / write_file / edit_file / glob / grep）、
执行命令（execute）、长对话压缩、大段工具输出存盘、子 agent（task）。Yip 补上的：搜索、读网页、打开网页检查、技能、
邮件、小队频道、连接器；写文件和执行命令记成产物；新约束和队友留言在每次调模型前插进对话。

Deep Agent 的对话存在同一个检查点库里，thread_id = deep:{task_id}:{deep_thread}；推翻、改派会换一个 deep_thread 从头来，
退回复查（reopen）沿用原来的，接着之前的对话改。
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from . import artifacts, db, events, memory, settings, team, verify
from .tools import Ctx, Tool, ToolError
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
from .workers import registry

SAVER = None          # worker.build() 传进来的检查点库，和外层图共用
MODEL_CALLS = 36      # 一段最多调几次模型。考题里给 80 时，查资料的活一个人跑了 117 步、28 分钟还停不下来
BUILTIN_RISK = {"write_file": "write", "edit_file": "write", "execute": "exec"}
LABEL = {"ls": "看文件夹", "read_file": "读取", "write_file": "写入", "edit_file": "修改", "glob": "找文件", "grep": "搜索文件",
         "execute": "执行", "write_todos": "列计划", "task": "派子任务"}


def engine() -> str:
    """队员用哪个引擎：环境变量 SA_ENGINE 优先（跑考题对比用），其次是设置。"""
    e = os.environ.get("SA_ENGINE") or settings.yip().get("engine") or "classic"
    return e if e in ("classic", "deep") else "classic"


def available() -> bool:
    try:
        import deepagents  # noqa: F401
        import langchain_openai  # noqa: F401
    except ImportError:
        return False
    return True


# ---------- 文件和命令：落在工作文件夹里，写出的东西记成产物 ----------
def _backend(ctx: Ctx):
    from deepagents.backends.local_shell import LocalShellBackend

    ws = ctx.workspace.resolve()

    class YipBackend(LocalShellBackend):
        """真实路径模式（模型写绝对路径不会被套一层），写和改只许在工作文件夹里。"""

        def _inside(self, file_path: str) -> Path | None:
            p = Path(file_path)
            p = (p if p.is_absolute() else ws / p).resolve()
            return p if p == ws or ws in p.parents else None

        async def awrite(self, file_path: str, content: str):
            from deepagents.backends.protocol import WriteResult
            p = self._inside(file_path)
            if not p:
                return WriteResult(error=f"只能写工作文件夹 {ws} 里的文件")
            r = await super().awrite(str(p), content)
            if not getattr(r, "error", None):
                artifacts.record(ctx, p)
            return r

        async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
            from deepagents.backends.protocol import EditResult
            p = self._inside(file_path)
            if not p:
                return EditResult(error=f"只能改工作文件夹 {ws} 里的文件")
            r = await super().aedit(str(p), old_string, new_string, replace_all)
            if not getattr(r, "error", None):
                artifacts.record(ctx, p)
            return r

        async def aexecute(self, command: str, *, timeout: int | None = None):
            # 自动 / 计划模式：一般命令直接跑，删东西、装软件、往工作文件夹外写这类才停下来问（和 HITL 同一种格式）
            pol = settings.yip()["approval"]
            grants = list((db.task(ctx.main_id) or {}).get("grants") or [])
            if pol in ("auto", "plan") and t_risk.decide("execute", "exec", pol, grants, {"command": command}, str(ws)) == "ask":
                from deepagents.backends.protocol import ExecuteResponse
                from langgraph.types import interrupt
                ans = interrupt({"action_requests": [{"name": "execute", "args": {"command": command}}]})
                d = ((ans or {}).get("decisions") or [{}])[0] if isinstance(ans, dict) else {}
                if d.get("type") == "reject":
                    return ExecuteResponse(output=d.get("message") or "主人没有同意这条命令", exit_code=1)
            before = t_shell._snapshot(ws)
            r = await super().aexecute(command, timeout=timeout)
            made = [p for p, t in t_shell._snapshot(ws).items() if before.get(p) != t][:20]
            for p in made:  # 命令生成的文件（脚本写出的 .docx 之类）也算产物
                artifacts.record(ctx, Path(p))
            return r

    return YipBackend(root_dir=str(ws), virtual_mode=False, inherit_env=True, timeout=120)


# ---------- Yip 自己的工具：包成 LangChain 工具 ----------
def _rel(ctx: Ctx, v: Any) -> Any:
    """Deep Agent 习惯写绝对路径；Yip 的工具要相对工作文件夹的路径。"""
    if isinstance(v, str) and v.startswith(str(ctx.workspace)):
        return v[len(str(ctx.workspace)):].lstrip("/") or "."
    return v


def _wrap(tool: Tool, ctx: Ctx):
    from langchain_core.tools import StructuredTool

    async def run(**kwargs) -> str:
        args = {k: _rel(ctx, v) for k, v in kwargs.items()}
        try:
            return await tool.run(ctx, **args)
        except ToolError as e:
            return f"出错：{e}"
        except TypeError as e:
            return f"参数不对：{e}"

    return StructuredTool.from_function(coroutine=run, name=tool.name, description=tool.description,
                                        args_schema=tool.parameters)


async def yip_tools(card, s: dict) -> tuple[list[Tool], list]:
    """老引擎的工具里去掉文件和命令（Deep Agent 自带更好用的），其余照旧。"""
    from .workers import registry as reg
    pref = reg.prefs(card.id)
    skills = t_skills.for_worker(card, pref["skills"])
    tools = t_web.TOOLS + t_skills.tools_for(card, skills)
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
    except Exception as e:  # noqa: BLE001
        events.event(s["task_id"], "error", f"连接器没连上：{type(e).__name__}")
    return tools, skills


def _brief(by: dict[str, Tool], name: str, args: dict) -> str:
    if name in by:
        return by[name].brief(args)
    path = str(args.get("file_path") or args.get("path") or args.get("pattern") or "")
    ws = str(settings.workspace())
    path = path[len(ws):].lstrip("/") if path.startswith(ws) else path
    if name == "write_file":
        n = len(str(args.get("content") or "").encode())
        return f"写入 {path} · {n / 1024:.1f} KB" if n >= 1024 else f"写入 {path} · {n} 字节"
    if name == "execute":
        return f"执行 {str(args.get('command') or '')[:80]}"
    if name == "write_todos":
        todos = args.get("todos") or []
        return f"列计划：{len(todos)} 项"
    if name == "task":
        return f"派子任务：{str(args.get('description') or '')[:40]}"
    return f"{LABEL.get(name, name)} {path}".strip()


# ---------- 每次调模型前后：插新约束和留言、记动态、看还该不该跑 ----------
def _middleware(s: dict, by: dict[str, Tool], counters: dict):
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.messages import HumanMessage

    tid = s["task_id"]
    rounds = max(int(settings.yip()["max_rounds"]), 16) * 3

    def alive() -> bool:
        t = db.task(tid)
        return bool(t and t["run_id"] == s["run_id"])

    class YipHooks(AgentMiddleware):
        async def abefore_model(self, state, runtime):
            if not alive():
                raise asyncio.CancelledError()
            add = []
            t = db.task(tid) or {}
            cons = list(t.get("constraints") or [])
            new = cons[counters["seen"]:]
            if new:
                events.event(tid, "constraint_applied", f"这一轮起按新要求做：{'；'.join(new)}")
                add.append(HumanMessage("【插话补充，从现在起照这个做，和之前冲突的以这里为准】\n"
                                        + "\n".join(f"- {c}" for c in new)))
                counters["seen"] = len(cons)
            if t.get("parent_id"):
                text, last = team.unread(t["parent_id"], tid, counters["team_seen"])
                if text:
                    add.append(HumanMessage(text))
                    counters["team_seen"] = last
            db.update("tasks", tid, note="正在想下一步")
            events.task_changed(tid)
            return {"messages": add} if add else None

        async def aafter_model(self, state, runtime):
            msg = state["messages"][-1] if state.get("messages") else None
            calls = getattr(msg, "tool_calls", None) or []
            text = msg.content if msg is not None and isinstance(msg.content, str) else ""
            if text.strip() and calls:
                events.event(tid, "thought", text[:300])
            return None

        async def awrap_tool_call(self, request, handler):
            call = request.tool_call
            name, args = call["name"], call.get("args") or {}
            brief = _brief(by, name, args)
            db.update("tasks", tid, note=brief)
            events.task_changed(tid)
            out = await handler(request)
            content = getattr(out, "content", out)
            text = content if isinstance(content, str) else str(content)
            bad = getattr(out, "status", "") == "error" or text.startswith(("出错", "Error"))
            if bad:
                events.event(tid, "tool_error", f"{brief} 没成功：{text[:120]}", tool=name)
            else:
                events.event(tid, "step", brief, tool=name, label=(by[name].label if name in by else LABEL.get(name, name)),
                             output=text[:600])
            counters["calls"] += 1
            db.update("tasks", tid, progress=round(min(counters["calls"] / rounds, 0.95), 3))
            return out

    return YipHooks()


def system_prompt(card, s: dict, skills) -> str:
    from .worker import _main_of, _relay
    y = settings.yip()
    main = _main_of(s["task_id"])
    folder = settings.workspace() / (main.get("title") or "").replace("/", "-").strip()
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
        f"- 这件事的项目文件夹是 {folder}/（绝对路径），大家都在里面干活；文件工具用绝对路径\n"
        "- 活不止两三步时先用 write_todos 列计划，做完一项勾一项\n"
        "- 开工先 ls 项目文件夹看看已经有什么；接着前面的人的文件改，不要另起一套\n"
        "- 公开信息先 web_search 再 web_fetch 看原文；只写查到的事实，不编数字，查不到写【待补充】\n"
        "- web_fetch 读不出来、要点按钮翻页、要登录才能看的网站，用 browser_open 打开，按编号 browser_click / browser_type；"
        "遇到登录页用 browser_login 请主人登录，不要自己猜账号密码；要网页截图用 browser_open 打开再 browser_screenshot，不要自己写脚本开浏览器\n"
        "- 要操作 Mac 上的应用（备忘录、日历、Finder…）：computer_open 打开，computer_look 看界面，按编号 computer_click / computer_type；"
        "每一步都会先问主人，所以步骤要少、要准\n"
        f"- 代码、网页这类能运行的东西直接写进项目文件夹（网页入口是 {folder}/index.html，双击能打开，不依赖构建和联网）；"
        "写完自己检查：脚本用 execute 跑一下，网页用 check_page 打开并跑脚本模拟操作\n"
        "- 要 PPT / Word / Excel / PDF：用 make_pptx / make_docx / make_xlsx / make_pdf 直接生成（给结构化的内容，工具排版），不要自己写脚本；读这类文件用 read_file\n"
        f"- 文字类结果（需求、调研、测试报告、审查意见）写成 Markdown：{folder}/{title}-{mine}.md；写完不要整篇复述\n"
        "- 日期：原文只写了月日、或者说「明天」「下周三」，年份和具体哪天按今天的日期推；写星期几前用 execute 跑 python3 -c 算一下，不要猜\n"
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


def _model(fast: bool = True):
    """日常模型打底；这块难、返工时用难活模型（worker.need_pro）。"""
    from langchain_openai import ChatOpenAI
    c = settings.resolve(fast)
    return ChatOpenAI(model=c["model"], base_url=c["base_url"], api_key=c["api_key"], timeout=180, max_retries=1,
                      temperature=0.2)


async def build_agent(s: dict, counters: dict):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware, TodoListMiddleware

    from .worker import ctx_of, _main_of
    card = registry.get(s["worker_id"]) or registry.general()
    tools, skills = await yip_tools(card, s)
    by = {t.name: t for t in tools}
    ctx = ctx_of(s)
    policy = settings.yip()["approval"]
    grants = list((db.task(_main_of(s["task_id"])["id"]) or {}).get("grants") or [])
    gated = {n: True for n, r in [*BUILTIN_RISK.items(), *[(t.name, t.risk) for t in tools]]
             if t_risk.decide(n, r, policy, grants) == "ask"}
    from .worker import escalate
    daily, up = escalate(s)
    counters.update(up)
    agent = create_deep_agent(
        model=_model(daily), tools=[_wrap(t, ctx) for t in tools], system_prompt=system_prompt(card, s, skills),
        backend=_backend(ctx), interrupt_on=gated or None, checkpointer=SAVER,
        middleware=[TodoListMiddleware(),  # deepagents 0.7 默认不带待办清单，补上
                    _middleware(s, by, counters), ModelCallLimitMiddleware(run_limit=MODEL_CALLS, exit_behavior="end")])
    return agent, by


def _cfg(s: dict) -> dict:
    return {"configurable": {"thread_id": f"deep:{s['task_id']}:{s.get('deep_thread') or s['run_id']}"},
            "recursion_limit": 1000}


def _requests(st) -> list[tuple[str, int, dict]]:
    """Deep Agent 停下来等放行的调用，每条是（interrupt id，序号，{name, args}）。"""
    out = []
    for it in st.interrupts or ():
        v = it.value if isinstance(it.value, dict) else {}
        for i, a in enumerate(v.get("action_requests") or []):
            out.append((it.id, i, a))
    return out


def _approval_rows(s: dict, by: dict[str, Tool], reqs) -> list[dict]:
    from .worker import _main_of
    main = _main_of(s["task_id"])
    rows = []
    for iid, i, a in reqs:
        key = f"{iid}:{i}"
        row = db.q1("SELECT * FROM approvals WHERE task_id=? AND run_id=? AND json_extract(args, '$.call_id')=?",
                    (s["task_id"], s["run_id"], key))
        if not row:
            name, args = a["name"], a.get("args") or {}
            shown = {k: (v[:4000] if isinstance(v, str) else v) for k, v in args.items()}
            aid = db.insert("approvals", task_id=s["task_id"], main_id=main["id"], run_id=s["run_id"],
                            worker_id=s["worker_id"], tool=name, args={"call_id": key, **shown},
                            summary=_brief(by, name, args), risk=by[name].risk if name in by else BUILTIN_RISK.get(name, "exec"),
                            status="pending", created_at=db.now())
            row = db.q1("SELECT * FROM approvals WHERE id=?", (aid,))
            events.event(s["task_id"], "approval", f"等你允许：{row['summary']}", approval_id=aid)
            events.publish("approval", row)
        rows.append(row)
    return rows


def _decisions(rows: list[dict], reqs) -> dict:
    """按放行结果回给 Deep Agent：{interrupt id: {"decisions": [...]}}。"""
    by_iid: dict[str, list] = {}
    for (iid, _i, a), row in zip(reqs, rows):
        if row["status"] == "denied":
            d = {"type": "reject", "message": "主人没有同意这个操作。换个不需要它的办法，或者在交付里说明缺了什么。"}
            events.event(row["task_id"], "denied", f"没被允许：{row['summary']}")
        else:
            d = {"type": "approve"}
        by_iid.setdefault(iid, []).append(d)
    return {iid: {"decisions": ds} for iid, ds in by_iid.items()}


async def deep(s: dict) -> dict:
    """跑一段 Deep Agent：直到它说做完了，或者有调用要人放行。"""
    from langchain_core.messages import AIMessage
    from langgraph.types import Command

    counters = {"seen": s.get("seen", 0), "team_seen": s.get("team_seen") or 0, "calls": s.get("step") or 0}
    agent, by = await build_agent(s, counters)
    cfg = _cfg(s)
    st = await agent.aget_state(cfg)
    reqs = _requests(st)
    if reqs:  # 上次停在放行：按人点的结果接着跑
        rows = _approval_rows(s, by, reqs)
        if any(r["status"] == "pending" for r in rows):
            return {"wait": [r["id"] for r in rows if r["status"] == "pending"]}
        inp: Any = Command(resume=_decisions(rows, reqs))
    else:
        texts = list(s.get("inbox") or []) or ([] if st.values.get("messages") else [s["goal"]])
        inp = {"messages": [{"role": "user", "content": t} for t in texts]}
    t0 = time.monotonic()
    await agent.ainvoke(inp, cfg)
    st = await agent.aget_state(cfg)
    out = {"inbox": [], "seen": counters["seen"], "team_seen": counters["team_seen"], "step": counters["calls"],
           **({"pro": True} if counters.get("pro") else {}),
           "deep_secs": round((s.get("deep_secs") or 0) + time.monotonic() - t0, 1)}
    reqs = _requests(st)
    if reqs:
        rows = _approval_rows(s, by, reqs)
        pending = [r for r in rows if r["status"] == "pending"]
        if pending:
            db.update("tasks", s["task_id"], note=f"等你允许：{pending[0]['summary']}")
            events.task_changed(s["task_id"])
            return {**out, "wait": [r["id"] for r in pending], "result": None}
        return {**out, "wait": [], "result": None, "again": True}  # 都已放行过（本次都允许）：直接接着跑
    last = next((m for m in reversed(st.values.get("messages") or []) if isinstance(m, AIMessage)), None)
    text = last.content if last is not None and isinstance(last.content, str) else ""
    return {**out, "wait": [], "again": False, "result": text.strip() or "（没有说明）"}


async def hold(s: dict) -> dict:
    """有调用等人放行：停住整张图，人点完 runner.resume 接着跑，回到 deep 节点按结果继续。"""
    from langgraph.types import interrupt
    interrupt({"approvals": s.get("wait") or []})
    return {"wait": []}


def route_deep(s: dict) -> str:
    if s.get("wait"):
        return "hold"
    if s.get("again"):
        return "deep"
    return "verify"
