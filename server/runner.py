"""run 注册表：启动 / 取消 worker，worker 跑完后按 seq 接上排队的事，全部跑完唤醒 Supervisor 汇总。"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path

from langgraph.types import Command

from . import db, events, llm, settings

TERMINAL = db.TERMINAL


def cfg(thread_id: str) -> dict:
    # 一轮工具调用走 4 个节点；最多 50 轮，返工每次再给 8 轮：600 够用，真死循环也停得住
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 600}


def _mode(worker_id: str | None = None) -> str:
    """队员这次用哪种方式干活：外部 ACP 队员走 acp；没配模型走演示；设置成 deep 且装了 deepagents 走 Deep Agents（试验）。"""
    from .workers import registry
    card = registry.get(worker_id) if worker_id else None
    if card and card.kind == "acp":
        return "acp"
    if not llm.configured():
        return "demo"
    from . import worker_deep
    return "deep" if worker_deep.engine() == "deep" and worker_deep.available() else "agent"


class Runner:
    def __init__(self, worker_app, sup_app):
        self.worker_app = worker_app
        self.sup_app = sup_app
        self.runs: dict[str, tuple[asyncio.Task, str]] = {}
        self.sups: dict[str, asyncio.Task] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self._sem: asyncio.Semaphore | None = None
        self._sem_n = 0

    def sem(self) -> asyncio.Semaphore:
        """最多同时几个队员在干活（设置 · 通用）；改了设置下一次开工时生效。"""
        n = int(settings.yip()["max_parallel"])
        if self._sem is None or n != self._sem_n:
            self._sem, self._sem_n = asyncio.Semaphore(n), n
        return self._sem

    # ---------- worker ----------
    async def start(self, task_id: str) -> str:
        await self.cancel(task_id)
        t = db.task(task_id)
        run_id = uuid.uuid4().hex[:8]
        self.void_approvals(task_id)
        db.update("tasks", task_id, run_id=run_id, status="running", progress=0, thread_id=f"task:{task_id}",
                  note="排队等开工", result=None)
        events.task_changed(task_id)
        cons = list(t["constraints"] or [])
        state = {
            "task_id": task_id, "run_id": run_id, "worker_id": t["worker_id"], "goal": t["goal"],
            "constraints": cons, "seen": len(cons), "scratchpad": [], "step": 0,
            "max_steps": max(2, min(int(t["steps"] or 4), 8)), "result": None,
            "mode": _mode(t["worker_id"]), "deep_thread": run_id,
            "messages": [{"role": "user", "content": t["goal"]}], "pending": [],
        }
        job = asyncio.create_task(self._run(task_id, run_id, state))
        self.runs[task_id] = (job, run_id)
        return run_id

    async def _run(self, task_id: str, run_id: str, state) -> None:
        try:
            async with self.sem():
                if isinstance(state, dict):
                    db.update("tasks", task_id, note="开始干活")
                    events.task_changed(task_id)
                async for _ in self.worker_app.astream(state, cfg(f"task:{task_id}")):
                    pass
            st = await self.worker_app.aget_state(cfg(f"task:{task_id}"))
            if st.next:  # 停在 gate 等人放行：不算结束，等 resume
                h = self.runs.get(task_id)
                if h and h[1] == run_id:
                    self.runs.pop(task_id, None)
                # 停下和登记之间人可能已经点完了（那时 resume 看到还在跑就跳过了）：自己再看一眼
                await self.resume(task_id)
                return
        except asyncio.CancelledError:
            return
        except Exception as e:
            t = db.task(task_id)
            if t and t["run_id"] == run_id:
                why = (llm.human_reason(str(e)) if isinstance(e, llm.LLMError)
                       else (str(e)[:160] if isinstance(e, RuntimeError) and str(e) else type(e).__name__))
                # 如果队员已经把有效文件落盘了，只是收尾汇报时模型连接抖动：按已交付收口，不把成果丢掉
                made = [a for a in db.q("SELECT name, path FROM artifacts WHERE task_id=?", (task_id,))
                        if Path(a["path"]).exists()] if isinstance(e, llm.LLMError) else []
                if made:
                    names = "、".join(a["name"] for a in made[:4])
                    db.update("tasks", task_id, status="done", progress=1.0,
                              note="已交付（收尾时网络波动）",
                              result=f"已生成文件：{names}（收尾汇报时遇到「{why}」，文件已保存）")
                    events.event(task_id, "step", f"文件已保存（收尾时遇到：{why}）")
                    events.task_changed(task_id)
                else:
                    db.update("tasks", task_id, status="failed", note=f"出错了：{why}")
                    events.event(task_id, "error", f"运行出错：{why}", detail=str(e)[:500])
                    events.task_changed(task_id)
        finally:
            h = self.runs.get(task_id)
            if h and h[1] == run_id:
                self.runs.pop(task_id, None)
        t = db.task(task_id)
        if t and t["run_id"] == run_id and t["status"] in TERMINAL:
            if t.get("redeliver") and t.get("parent_id"):  # 后台细看退回的改好了：再交一版
                db.update("tasks", task_id, redeliver=0)
                from .graph import redeliver
                await redeliver(t["parent_id"])
                return
            self.ping(t)
            await self.hand_back(t)
            await self.release_queue(task_id)
            if t["parent_id"]:
                await self.check_parent(t["parent_id"])

    async def reopen(self, task_id: str, text: str, *, return_to: str | None = None, note: str = "") -> str:
        """让做完的队员接着改：保留它之前的对话，补一句话再跑（队友退回、改完复查都走这里）。"""
        t = db.task(task_id)
        # 先把状态改成在做：免得这一刻别处 check_parent 看到「全做完了」就去汇总
        db.update("tasks", task_id, status="running", note=note or "接着改", result=None,
                  **({"return_to": return_to} if return_to else {}))
        events.task_changed(task_id)
        st = await self.worker_app.aget_state(cfg(f"task:{task_id}"))
        v = dict(st.values or {})
        if v.get("mode") in ("deep", "acp"):  # Deep Agent / 外部 ACP 队员：塞一句话接着跑
            await self.cancel(task_id)
            run_id = uuid.uuid4().hex[:8]
            v.update(run_id=run_id, inbox=[text], result=None, reworks=0, verdict=None, wait=[], again=False)
            db.update("tasks", task_id, run_id=run_id, progress=0.5, verdict=None)
            events.event(task_id, "reopened", note or "接着改")
            job = asyncio.create_task(self._run(task_id, run_id, v))
            self.runs[task_id] = (job, run_id)
            return run_id
        if not v.get("messages") or v.get("mode") != "agent":  # 没有可接着的对话（演示模式）：带着这句话重跑
            db.update("tasks", task_id, constraints=list(t["constraints"] or []) + [text[:300]])
            return await self.start(task_id)
        await self.cancel(task_id)
        run_id = uuid.uuid4().hex[:8]
        rounds = int(settings.yip()["max_rounds"])
        v.update(run_id=run_id, messages=v["messages"] + [{"role": "user", "content": text}], result=None,
                 pending=[], reworks=0, verdict=None, step=max(0, min(v.get("step") or 0, rounds - 8)))
        db.update("tasks", task_id, run_id=run_id, progress=0.5, verdict=None)
        events.event(task_id, "reopened", note or "接着改")
        job = asyncio.create_task(self._run(task_id, run_id, v))
        self.runs[task_id] = (job, run_id)
        return run_id

    def ping(self, t: dict) -> None:
        """接力时说一声：A 那块好了，B 接着做。只在关键节点冒泡，过程细节在看板里。"""
        if not t.get("parent_id") or t.get("return_to"):
            return
        from .workers import registry
        kids = db.children(t["parent_id"])
        if len(kids) < 2:
            return
        if t["status"] == "failed":
            events.message(t["parent_id"], "assistant", f"{t['title']}没做成：{(t.get('note') or '').removeprefix('出错了：')}")
            return
        if t["status"] != "done":
            return
        nxt = [k for k in kids if k["status"] == "waiting" and t["id"] in (k.get("prereqs") or [])
               and all((db.task(p) or {}).get("status") in TERMINAL for p in k.get("prereqs") or [])]
        if nxt:
            card = registry.get(nxt[0]["worker_id"])
            me = registry.get(t["worker_id"])
            who = "" if card and me and card.id == me.id else (card.name if card else "")
            events.message(t["parent_id"], "assistant", f"{t['title']}好了，{who}接着{nxt[0]['title']}")

    async def plan_go(self, main_id: str) -> None:
        """计划模式：主人说开工。"""
        db.update("tasks", main_id, plan_ok=1, note="执行中")
        events.task_changed(main_id)
        self._spawn_sup(main_id, Command(resume=True))

    async def plan_revise(self, main_id: str, text: str) -> None:
        """计划模式：主人改了计划，带着他的话重新拆，拆完再给他看。"""
        t = db.task(main_id)
        await self.cancel_main(main_id)
        db.update("tasks", main_id, constraints=list(t["constraints"] or []) + [text[:300]], note="正在重新拆", plan_ok=0,
                  outline=None)
        events.task_changed(main_id)
        self.start_main(main_id)

    async def hand_back(self, t: dict) -> None:
        """被退回的人改完了：叫退回的人复查。"""
        rid = t.get("return_to")
        if not rid:
            return
        db.update("tasks", t["id"], return_to=None)
        req = db.task(rid)
        if not req or req["status"] not in TERMINAL or req["status"] == "cancelled":
            return
        from .team import MAX_BOUNCE, post
        from .workers import registry
        ok = t["status"] == "done"
        body = (t["result"] or "").strip()[:1500]
        post(t["parent_id"], t["id"], f"改好了，请复查：{body[:200]}" if ok else "没改完", to_task=rid)
        await self.reopen(rid, (
            f"{registry.part_name(t)} 按你退回的问题{'改好了' if ok else '没改完（中断了）'}，他的交代：\n{body or '没有说明'}\n"
            f"再检查一遍。还有问题可以再 send_back（每人最多被退回 {MAX_BOUNCE} 次），没问题就照常交付。"),
            note=f"复查{registry.part_name(t)}改的")

    def void_approvals(self, task_id: str) -> None:
        """换了一次运行（推翻、改派、取消）：上一次等着的放行请求一起作废。"""
        from . import worker_acp
        rows = db.q("SELECT id FROM approvals WHERE task_id=? AND status='pending'", (task_id,))
        for r in rows:
            db.update("approvals", r["id"], status="cancelled", decided_at=db.now())
            worker_acp.notify_approval(r["id"], "deny")
        if rows:
            events.publish("approval", {"task_id": task_id, "status": "cancelled"})

    async def resume(self, task_id: str) -> bool:
        """放行请求都定了，就让停在 gate 的队员接着跑。"""
        t = db.task(task_id)
        if not t or t["status"] in TERMINAL or task_id in self.runs:
            return False
        left = db.q1("SELECT COUNT(*) AS n FROM approvals WHERE task_id=? AND run_id=? AND status='pending'",
                     (task_id, t["run_id"]))["n"]
        if left:
            return False
        st = await self.worker_app.aget_state(cfg(f"task:{task_id}"))
        if not st.next:
            return False
        job = asyncio.create_task(self._run(task_id, t["run_id"], Command(resume=True)))
        self.runs[task_id] = (job, t["run_id"])
        return True

    async def cancel(self, task_id: str) -> None:
        self.void_approvals(task_id)
        h = self.runs.pop(task_id, None)
        if h:
            h[0].cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await h[0]

    def running(self, task_id: str) -> bool:
        return task_id in self.runs

    # ---------- 排队 ----------
    async def release_queue(self, target_id: str) -> int:
        rows = db.q("SELECT * FROM task_queue WHERE task_id=? AND consumed=0 ORDER BY seq", (target_id,))
        n = 0
        for r in rows:
            db.update("task_queue", r["id"], consumed=1)
            sid = (r["payload"] or {}).get("subtask_id")
            st = db.task(sid) if sid else None
            if st and st["status"] == "waiting":
                events.event(sid, "dequeued", f"轮到了，开始做「{st['title']}」")
                await self.start(sid)
                n += 1
        return n

    async def start_ready(self, kids: list[dict]) -> int:
        """接力：前面的都交了（或停了），后面等着的就开工。"""
        done = {k["id"] for k in kids if k["status"] in TERMINAL}
        by = {k["id"]: k for k in kids}

        def ancestors(k: dict) -> set[str]:
            out, todo = set(), list(k.get("prereqs") or [])
            while todo:
                x = todo.pop()
                if x not in out and x in by:
                    out.add(x)
                    todo += by[x].get("prereqs") or []
            return out
        n = 0
        for k in kids:
            pre = k.get("prereqs") or []
            # 前面的都交了，而且前面链上没有人正被退回返工 / 复查
            if k["status"] == "waiting" and pre and all(p in done for p in pre) and not self.running(k["id"]) \
                    and all(by[a]["status"] in TERMINAL for a in ancestors(k)):
                events.event(k["id"], "dequeued", f"前面交了，开始做「{k['title']}」")
                await self.start(k["id"])
                n += 1
        return n

    async def check_parent(self, pid: str) -> None:
        lock = self.locks.setdefault(pid, asyncio.Lock())
        async with lock:
            kids = db.children(pid)
            if await self.start_ready(kids):
                return
            blocking = [k for k in kids if k["status"] not in TERMINAL
                        and not (k["status"] == "waiting" and k["depends_on"] == pid)]
            if blocking:
                return
            if await self.release_queue(pid):
                return
            if any(k["status"] not in TERMINAL for k in kids):
                return
            main = db.task(pid)
            if not main or main["status"] in TERMINAL:
                return
            if main.get("kind") == "trial":  # 试用：一个队员单干，不走 Yip 汇总
                k = kids[0]
                db.update("tasks", pid, status="done" if k["status"] == "done" else k["status"],
                          result=k["result"], progress=1.0, note="试用结束")
                events.event(pid, "trial_done", "试用结束")
                events.task_changed(pid)
                return
            job = self.sups.get(pid)
            if job and not job.done():  # 还在拆解 / 派发，collect 会自己看到全部完成
                return
            st = await self.sup_app.aget_state(cfg(f"main:{pid}"))
            if not st.next:
                return
            self._spawn_sup(pid, Command(resume=True))

    # ---------- supervisor ----------
    def _spawn_sup(self, main_id: str, payload) -> None:
        old = self.sups.get(main_id)
        if old and not old.done() and not isinstance(payload, Command):
            old.cancel()
        job = asyncio.create_task(self._run_sup(main_id, payload))
        self.sups[main_id] = job

    async def _run_sup(self, main_id: str, payload) -> None:
        from .graph import _all_done
        try:
            while True:
                await self.sup_app.ainvoke(payload, cfg(f"main:{main_id}"))
                st = await self.sup_app.aget_state(cfg(f"main:{main_id}"))
                # 停在 collect 的这一刻子任务恰好都跑完了：自己接着走，不等别人唤醒
                if not (st.next and _all_done(main_id)):
                    break
                payload = Command(resume=True)
        except asyncio.CancelledError:
            return
        except Exception as e:
            why = llm.human_reason(str(e)) if isinstance(e, llm.LLMError) else type(e).__name__
            db.update("tasks", main_id, status="failed", note=f"出错了：{why}")
            events.event(main_id, "error", f"编排出错：{type(e).__name__}", detail=str(e)[:500])
            events.task_changed(main_id)

    async def watchdog(self, stuck_ms: int = 150_000, every: float = 30) -> None:
        """拆解卡住（模型没回、连接挂住）就重来；重来两次还卡就标成中断，不让这件事一直挂在「正在拆解」。"""
        tries: dict[str, int] = {}
        while True:
            await asyncio.sleep(every)
            try:
                for t in db.q("SELECT * FROM tasks WHERE parent_id IS NULL AND status='running' AND note='正在拆解' "
                              "AND created_at<?", (db.now() - stuck_ms,)):
                    if db.children(t["id"]):
                        continue
                    n = tries.get(t["id"], 0)
                    if n >= 2:
                        db.update("tasks", t["id"], status="failed", note="拆解卡住了：模型一直没回应")
                        events.event(t["id"], "error", "拆解卡住，重试 2 次都没成")
                        events.task_changed(t["id"])
                        continue
                    if t["updated_at"] > db.now() - stuck_ms:
                        continue
                    tries[t["id"]] = n + 1
                    old = self.sups.pop(t["id"], None)
                    if old and not old.done():
                        old.cancel()
                    db.update("tasks", t["id"], note="正在拆解")
                    events.event(t["id"], "restarted", f"拆解卡住了，重来第 {n + 1} 次")
                    self.start_main(t["id"])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 看门狗自己不能死
                pass

    def start_main(self, main_id: str) -> None:
        t = db.task(main_id)
        self._spawn_sup(main_id, {
            "main_id": main_id, "goal": t["goal"], "constraints": list(t["constraints"] or []),
            "subtasks": [], "artifacts": [], "memories": [],
        })

    async def cancel_main(self, main_id: str, *, delete_children: bool = False) -> None:
        sup = self.sups.pop(main_id, None)
        if sup and not sup.done():
            sup.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sup
        for k in db.children(main_id):
            await self.cancel(k["id"])
            if delete_children:
                db.ex("DELETE FROM tasks WHERE id=?", (k["id"],))
            elif k["status"] not in TERMINAL:
                db.update("tasks", k["id"], status="cancelled", note="已取消")
                events.task_changed(k["id"])
        db.ex("UPDATE task_queue SET consumed=1 WHERE task_id=? OR task_id IN (SELECT id FROM tasks WHERE parent_id=?)",
              (main_id, main_id))
        stale = db.q("SELECT id FROM decisions WHERE task_id=? AND status='pending_confirm'", (main_id,))
        for d in stale:
            db.update("decisions", d["id"], status="cancelled")
            events.publish("decision", {"id": d["id"], "task_id": main_id, "status": "cancelled"})


RUNNER: Runner | None = None


def get() -> Runner:
    assert RUNNER is not None, "runner 未初始化"
    return RUNNER
