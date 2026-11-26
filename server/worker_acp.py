"""外部队员的通用 ACP（Agent Client Protocol）执行引擎。

通过标准 JSON-RPC 2.0 over stdio 驱动本机官方外部 Agent（agy / hermes / codex 等）：
- 启动前按外部队员维度加并发闸门（默认 1），防止并发刷量触发风控；
- 将子进程 cwd 锁定在当前任务的项目文件夹内；
- 实时转发思考、工具调用到 Yip 动态；
- 拦截 session/request_permission，接入 Yip 的「等你确认」抽屉；
- 任务取消/推翻时发送 session/cancel 并优雅终止子进程；
- 执行前后对比工作文件夹快照，自动把外部 Agent 写出的文件登记为产物。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from . import artifacts, db, events, external, settings, team, verify
from .tools import Ctx
from .tools import shell as t_shell
from .workers import registry

_SEMS: dict[str, asyncio.Semaphore] = {}
_APPROVAL_WAITERS: dict[int, asyncio.Future[str]] = {}


def _sem(worker_id: str) -> asyncio.Semaphore:
    spec = external.BY_ID.get(worker_id)
    limit = spec.max_concurrency if spec else 1
    if worker_id not in _SEMS:
        _SEMS[worker_id] = asyncio.Semaphore(max(1, limit))
    return _SEMS[worker_id]


def notify_approval(aid: int, decision: str) -> None:
    """审批抽屉做出决定后，立刻唤醒正在等放行的 ACP 子进程。"""
    fut = _APPROVAL_WAITERS.get(aid)
    if fut and not fut.done():
        fut.set_result(decision)


def _alive(s: dict) -> bool:
    t = db.task(s["task_id"])
    return bool(t and t["run_id"] == s["run_id"])


def _main_of(task_id: str) -> dict:
    t = db.task(task_id) or {}
    return db.task(t["parent_id"]) if t.get("parent_id") else t


def _project_dir(s: dict) -> Path:
    ws = settings.workspace().resolve()
    main = _main_of(s["task_id"])
    folder = (main.get("title") or "任务").replace("/", "-").strip() or "任务"
    p = (ws / folder).resolve()
    if ws not in p.parents and p != ws:
        p = ws
    p.mkdir(parents=True, exist_ok=True)
    return p


def _build_prompt(card, s: dict, project_dir: Path) -> str:
    main = _main_of(s["task_id"])
    me = db.task(s["task_id"]) or {}
    cons = s.get("constraints") or []
    parts = [
        f"你正在作为 Yip 团队的外部队员「{card.name}」执行子任务。",
        f"## 这次的活（今天是 {verify.today()}）\n{s['goal']}",
        f"## 整件事背景\n{main.get('goal') or s['goal']}",
        f"## 工作目录约束\n- 只能在当前项目目录 `{project_dir}` 内读写文件，不要修改目录外的文件。",
    ]
    if me.get("accept"):
        parts.append("## 验收标准\n" + "\n".join(f"- {a}" for a in me["accept"]))
    if cons:
        parts.append("## 必须遵守的约束（以最新为准）\n" + "\n".join(f"- {c}" for c in cons))
    # 前序接力信息
    relays = []
    for pid in me.get("prereqs") or []:
        pt = db.task(pid)
        if pt:
            pc = registry.get(pt["worker_id"])
            relays.append(f"- {pt['title']}（{pc.name if pc else pt['worker_id']}）：{(pt.get('result') or '无说明').strip()[:800]}")
    if relays:
        parts.append("## 前面队友已交付\n" + "\n".join(relays))
    # 返工或复查意见
    inbox = s.get("inbox") or []
    if inbox:
        parts.append("## 最新修改意见\n" + "\n\n".join(inbox))
    parts.append("## 交付要求\n完成后用 3 到 6 行简洁中文说明：做了什么、生成或修改了哪些文件、怎么验证、还有什么未完成。")
    return "\n\n".join(parts)


class ACPClient:
    """轻量级异步 ACP（JSON-RPC 2.0 over stdio）客户端。"""

    def __init__(self, cmd: list[str], cwd: Path, env: dict[str, str], *, on_update=None, on_permission=None):
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.on_update = on_update
        self.on_permission = on_permission
        self.proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_lines: list[str] = []
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._write_lock = asyncio.Lock()
        self.session_id: str | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            cwd=str(self.cwd),
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _send_raw(self, payload: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("ACP 进程未启动")
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.proc.stdin.write(line)
            await self.proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float = 120.0) -> Any:
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send_raw({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict) -> None:
        with contextlib.suppress(Exception):
            await self._send_raw({"jsonrpc": "2.0", "method": method, "params": params})

    async def _reply(self, msg_id: Any, *, result: Any = None, error: dict | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result
        await self._send_raw(payload)

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self._stderr_lines.append(text)
                    if len(self._stderr_lines) > 30:
                        self._stderr_lines = self._stderr_lines[-30:]
        except asyncio.CancelledError:
            return

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text or not text.startswith("{"):
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if "id" in msg and "method" not in msg:
                    fut = self._pending.get(msg["id"])
                    if fut and not fut.done():
                        if "error" in msg and msg["error"] is not None:
                            err = msg["error"]
                            detail = err.get("message") if isinstance(err, dict) else str(err)
                            fut.set_exception(RuntimeError(f"ACP 错误：{detail}"))
                        else:
                            fut.set_result(msg.get("result"))
                elif "method" in msg and "id" not in msg:
                    if msg["method"] == "session/update" and self.on_update:
                        with contextlib.suppress(Exception):
                            await self.on_update(msg.get("params") or {})
                elif "method" in msg and "id" in msg:
                    asyncio.create_task(self._handle_server_request(msg["id"], msg["method"], msg.get("params") or {}))
        except asyncio.CancelledError:
            return
        finally:
            err_tail = " | ".join(self._stderr_lines[-3:])
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError(f"外部队员进程提前退出{('：' + err_tail) if err_tail else ''}"))

    async def _handle_server_request(self, msg_id: Any, method: str, params: dict) -> None:
        if method == "session/request_permission" and self.on_permission:
            try:
                res = await self.on_permission(params)
                await self._reply(msg_id, result=res)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await self._reply(msg_id, result={"outcome": {"outcome": "cancelled"}})
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    await self._reply(msg_id, error={"code": -32000, "message": str(e)})
            return
        with contextlib.suppress(Exception):
            await self._reply(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})

    async def initialize(self) -> dict:
        return await self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "Yip", "version": "1.0.0"},
        }, timeout=45.0)

    async def new_session(self) -> str:
        res = await self.request("session/new", {"cwd": str(self.cwd), "mcpServers": []}, timeout=45.0)
        sid = (res or {}).get("sessionId")
        if not sid:
            raise RuntimeError("外部队员未返回 sessionId")
        self.session_id = str(sid)
        return self.session_id

    async def prompt(self, text: str, timeout: float = 900.0) -> dict:
        if not self.session_id:
            raise RuntimeError("尚未建立 ACP 会话")
        return await self.request("session/prompt", {
            "sessionId": self.session_id,
            "prompt": [{"type": "text", "text": text}],
        }, timeout=timeout)

    async def cancel(self) -> None:
        if self.session_id:
            await self.notify("session/cancel", {"sessionId": self.session_id})

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
        for t in (self._reader_task, self._stderr_task):
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t


def _pick_option(options: list[dict], kinds: tuple[str, ...]) -> str | None:
    for kind in kinds:
        for opt in options:
            if opt.get("kind") == kind and opt.get("optionId"):
                return str(opt["optionId"])
    return None


async def acp_node(s: dict) -> dict:
    """LangGraph 队员节点：调用外部 ACP 队员完成当前任务。"""
    wid = s["worker_id"]
    card = registry.get(wid)
    spec = external.BY_ID.get(wid)
    if not card or not spec:
        raise RuntimeError(f"找不到外部队员：{wid}")
    cfg = external.get_entry(wid)
    pr = external.probe(wid, cfg["command"])
    if not pr["installed"] or not pr["cmd"]:
        raise RuntimeError(f"{spec.name} 未就绪：{pr['reason']}")

    tid, run_id = s["task_id"], s["run_id"]
    main = _main_of(tid)
    ws = settings.workspace().resolve()
    project_dir = _project_dir(s)
    ctx = Ctx(task_id=tid, main_id=main.get("id", tid), worker_id=wid, workspace=ws,
              constraints=list(s.get("constraints") or []))

    chunks: list[str] = []
    thought_buf: list[str] = []
    step_count = 0

    async def on_update(params: dict) -> None:
        nonlocal step_count
        if not _alive(s):
            return
        upd = params.get("update") or params
        kind = upd.get("sessionUpdate") or ""
        if kind == "agent_message_chunk":
            content = upd.get("content") or {}
            txt = content.get("text") if isinstance(content, dict) else ""
            if txt:
                chunks.append(txt)
        elif kind == "agent_thought_chunk":
            content = upd.get("content") or {}
            txt = content.get("text") if isinstance(content, dict) else ""
            if txt:
                thought_buf.append(txt)
                joined = "".join(thought_buf)
                if len(joined) >= 120 or "\n" in txt:
                    events.event(tid, "thought", joined.strip()[:300])
                    thought_buf.clear()
        elif kind in ("tool_call", "tool_call_update"):
            title = str(upd.get("title") or upd.get("name") or "执行操作").strip()[:80]
            status = str(upd.get("status") or "")
            step_count += 1
            prog = round(min(0.1 + step_count * 0.06, 0.92), 3)
            db.update("tasks", tid, note=title, progress=prog)
            events.task_changed(tid)
            if kind == "tool_call" or status in ("completed", "failed"):
                out_text = ""
                for item in upd.get("content") or []:
                    if isinstance(item, dict):
                        c = item.get("content")
                        if isinstance(c, dict) and c.get("text"):
                            out_text += str(c["text"])[:400]
                ev_type = "tool_error" if status == "failed" else "step"
                events.event(tid, ev_type, title, tool=spec.id, label=spec.name, output=out_text[:600])

    async def on_permission(params: dict) -> dict:
        options = params.get("options") or []
        tc = params.get("toolCall") or {}
        title = str(tc.get("title") or tc.get("kind") or f"{spec.name} 请求执行操作").strip()[:120]
        tool_key = f"acp:{spec.id}:{tc.get('kind') or 'exec'}"

        allow_once = _pick_option(options, ("allow_once", "allow_always"))
        allow_always = _pick_option(options, ("allow_always", "allow_once"))
        reject_once = _pick_option(options, ("reject_once", "reject_always"))

        pol = settings.yip()["approval"]
        grants = list((db.task(main["id"]) or {}).get("grants") or [])
        if pol == "allow_all" or tool_key in grants:
            opt = allow_always or allow_once
            return {"outcome": {"outcome": "selected", "optionId": opt}} if opt else {"outcome": {"outcome": "cancelled"}}

        aid = db.insert(
            "approvals",
            task_id=tid,
            main_id=main["id"],
            run_id=run_id,
            worker_id=wid,
            tool=tool_key,
            args={"title": title, "kind": tc.get("kind") or "exec"},
            summary=f"{spec.name}：{title}",
            risk="exec",
            status="pending",
            created_at=db.now(),
        )
        row = db.q1("SELECT * FROM approvals WHERE id=?", (aid,))
        events.event(tid, "approval", f"等你允许：{row['summary']}", approval_id=aid)
        events.publish("approval", row)
        db.update("tasks", tid, note=f"等你允许：{title}")
        events.task_changed(tid)

        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        _APPROVAL_WAITERS[aid] = fut
        try:
            decision = await fut
        finally:
            _APPROVAL_WAITERS.pop(aid, None)

        if decision == "allow_task" and allow_always:
            return {"outcome": {"outcome": "selected", "optionId": allow_always}}
        if decision in ("allow", "allow_task") and allow_once:
            return {"outcome": {"outcome": "selected", "optionId": allow_once}}
        if reject_once:
            return {"outcome": {"outcome": "selected", "optionId": reject_once}}
        return {"outcome": {"outcome": "cancelled"}}

    before = t_shell._snapshot(ws)
    prompt_text = _build_prompt(card, s, project_dir)
    if spec.protocol == "cli":
        cmd = list(pr["cmd"])
        use_stdin = bool(cmd and cmd[-1] == "-")
        if not use_stdin:
            cmd.append(prompt_text)
        async with _sem(wid):
            db.update("tasks", tid, note=f"{spec.name} 正在干活", progress=0.15)
            events.task_changed(tid)
            events.event(tid, "step", f"启动外部队员：{spec.name}", tool=spec.id, label=spec.name)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_dir),
                env=external.runtime_env(wid),
                stdin=asyncio.subprocess.PIPE if use_stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(prompt_text.encode("utf-8") if use_stdin else None),
                    timeout=900.0,
                )
            except asyncio.CancelledError:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                raise
            finally:
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
            stdout_txt = out_b.decode("utf-8", errors="replace").strip()
            stderr_txt = err_b.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not stdout_txt:
                raise RuntimeError(f"{spec.name} 执行失败（退出码 {proc.returncode}）：{stderr_txt[-300:]}")
            if stdout_txt:
                chunks.append(stdout_txt)
    else:
        client = ACPClient(pr["cmd"], project_dir, external.runtime_env(wid),
                           on_update=on_update, on_permission=on_permission)
        async with _sem(wid):
            db.update("tasks", tid, note=f"连接 {spec.name}…", progress=0.05)
            events.task_changed(tid)
            events.event(tid, "step", f"启动外部队员：{spec.name}", tool=spec.id, label=spec.name)
            try:
                await client.start()
                await client.initialize()
                await client.new_session()
                db.update("tasks", tid, note=f"{spec.name} 正在干活", progress=0.1)
                events.task_changed(tid)
                await client.prompt(prompt_text)
            except asyncio.CancelledError:
                await client.cancel()
                raise
            finally:
                await client.close()

    if not _alive(s):
        raise asyncio.CancelledError()

    if thought_buf:
        events.event(tid, "thought", "".join(thought_buf).strip()[:300])

    # 扫描工作文件夹内新增或修改的文件，自动登记为产物
    made = [p for p, mt in t_shell._snapshot(ws).items() if before.get(p) != mt][:30]
    for p in made:
        artifacts.record(ctx, Path(p))

    result = "".join(chunks).strip()
    if not result and made:
        names = "、".join(Path(p).name for p in made[:5])
        result = f"已完成，共更新文件：{names}。"
    return {"result": result or f"{spec.name} 已完成任务。", "inbox": []}
