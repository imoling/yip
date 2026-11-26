"""FastAPI：REST + SSE + 静态托管 app/dist（先在 app/ 里 npm run build）。

和 Yip 只有一条对话：/api/say 进来先分流（闲聊 / 新的活 / 插话），/api/state 一次拿齐对话、事、待确认、产物。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import secrets
import shutil
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from . import artifacts, automation, db, events, goals, graph, llm, memory, runner, settings, starters, team, uploads, worker
from .router import actions, classify, front
from .tools import mcp as t_mcp
from .tools import mail as t_mail
from .tools import mcp_presets
from .tools import skills as t_skills
from .schema import Assign, Interject, NewTask, Override
from .workers import author, registry

DIST = db.ROOT / "app" / "dist"
WEB = DIST
# Tauri 页面（macOS / Windows 两种写法）和 Vite 开发地址
ALLOWED_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "http://localhost:1420"]


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    registry.reload()
    for t in db.q("SELECT id FROM tasks WHERE status IN ('running','pending','waiting')"):
        db.update("tasks", t["id"], status="failed", note="服务重启，已中断")
    db.ex("UPDATE approvals SET status='cancelled' WHERE status='pending'")
    async with AsyncSqliteSaver.from_conn_string(str(db.DATA_DIR / "checkpoints.db")) as cp:
        runner.RUNNER = runner.Runner(worker.build(cp), graph.build(cp))
        auto = asyncio.create_task(automation.loop(lambda text, shown: create_main(text, shown=shown, kind="auto")))
        goal_loop = asyncio.create_task(goals.loop(lambda text, shown, gid: create_main(text, shown=shown, kind="goal", goal_id=gid)))
        dog = asyncio.create_task(runner.RUNNER.watchdog())
        from . import soul
        nudger = asyncio.create_task(soul.loop())
        yield
        nudger.cancel()
        goal_loop.cancel()
        auto.cancel()
        dog.cancel()
        for tid in list(runner.RUNNER.runs):
            await runner.RUNNER.cancel(tid)
        for job in runner.RUNNER.sups.values():
            job.cancel()
        await t_mcp.shutdown()
        from .tools import browser
        with contextlib.suppress(Exception):
            await browser.B.close()


class TokenGuard:
    """设了 SA_API_TOKEN 时，/api/* 要带 `Authorization: Bearer <令牌>`；GET 也可用 `?token=`（EventSource 带不了请求头）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = os.environ.get("SA_API_TOKEN", "")
        if token and scope["type"] in ("http", "websocket") and scope.get("path", "").startswith("/api/"):
            if scope["type"] == "http" and scope["method"] != "OPTIONS" and not _token_ok(scope, token):
                await JSONResponse({"detail": "令牌无效"}, 401)(scope, receive, send)
                return
            if scope["type"] == "websocket" and not _token_ok(scope, token, is_ws=True):
                await send({"type": "websocket.close", "code": 4401})
                return
        await self.app(scope, receive, send)


def _token_ok(scope, token: str, *, is_ws: bool = False) -> bool:
    got = ""
    for k, v in scope.get("headers") or []:
        if k == b"authorization":
            auth = v.decode("latin-1")
            if auth[:7].lower() == "bearer ":
                got = auth[7:].strip()
            break
    if not got and (is_ws or scope.get("method") in ("GET", "HEAD")):
        got = (parse_qs(scope.get("query_string", b"").decode("latin-1")).get("token") or [""])[0]
    return bool(got) and secrets.compare_digest(got.encode(), token.encode())


app = FastAPI(title="Yip", lifespan=lifespan)
app.add_middleware(TokenGuard)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def _404(what="任务"):
    raise HTTPException(404, f"{what}不存在")


@app.get("/api/health")
async def health():
    return {"ok": True, "pid": os.getpid()}


def _extract_mention(text: str) -> tuple[str | None, str]:
    """解析输入框里的 @队员名（或 @队员id），返回 (worker_id, 去掉 @ 后的任务正文)。"""
    raw = text.strip()
    if not raw.startswith("@"):
        return None, raw
    cards = sorted(registry.active(), key=lambda c: -len(c.name))
    lower = raw[1:].lower()
    for c in cards:
        for token in (c.name, c.id):
            t = token.lower()
            if lower.startswith(t):
                rest = raw[1 + len(token):]
                if not rest or rest[0].isspace() or rest[0] in ("，", ",", "：", ":", "、"):
                    clean = rest.lstrip(" \t，,：:、").strip()
                    return c.id, clean
    return None, raw


# ---------- 任务 ----------
def create_main(goal: str, *, shown: str | None = None, attachments: list | None = None, kind: str = "",
                plan_first: bool = False, goal_id: str | None = None, worker_id: str | None = None) -> dict:
    tid = db.new_id()
    title = (goal if (kind == "auto" or worker_id) else (shown or goal)).strip().rstrip("。")
    for p in ("帮我", "请", "麻烦"):
        if title.startswith(p):
            title = title[len(p):]
    for p in ("整理一份", "整理一下", "整理", "做一份", "做个", "看一下", "看下", "查一下", "查查", "调研一下", "写一份", "写个"):
        if title.startswith(p):
            title = title[len(p):]
            break
    title = title.split("，")[0].split(",")[0].split("。")[0].replace("的", "", 1)[:16].strip() or "新的事"
    db.insert("tasks", id=tid, parent_id=None, label="", title=title, goal=goal, constraints=[], status="running",
              note="正在拆解", thread_id=f"main:{tid}", kind=kind, plan_first=int(plan_first), goal_id=goal_id,
              worker_id=worker_id, created_at=db.now(), updated_at=db.now())
    events.message(tid, "user", shown or goal, **({"attachments": attachments} if attachments else {}))
    events.event(tid, "created", "任务创建")
    events.task_changed(tid)
    runner.get().start_main(tid)
    return db.task(tid)


@app.post("/api/tasks")
async def new_task(body: NewTask):
    if not body.goal.strip():
        raise HTTPException(400, "说一下要做什么")
    return create_main(body.goal.strip())


@app.get("/api/tasks")
async def list_tasks():
    out = []
    for m in db.main_tasks():
        m["children"] = db.children(m["id"])
        m["memories"] = db.q1("SELECT COUNT(*) AS n FROM memories WHERE task_id=?", (m["id"],))["n"]
        out.append(m)
    return out




def _delivered(t: dict) -> list[dict]:
    """这件事交给主人的文件：交付那条消息里带的；老数据没有就按交付规则从产物里挑，不算队员笔记。"""
    for m in reversed(db.q("SELECT meta FROM messages WHERE task_id=? AND role='assistant' ORDER BY id", (t["id"],))):
        meta = m.get("meta") or {}
        if isinstance(meta, dict) and meta.get("final"):
            ids = [i for i in [meta.get("artifact_id"), *(meta.get("files") or [])] if isinstance(i, int)]
            if ids:
                ph = ",".join("?" * len(ids))
                got = {a["id"]: a for a in db.q(f"SELECT * FROM artifacts WHERE id IN ({ph})", tuple(ids))}
                return [got[i] for i in dict.fromkeys(ids) if i in got]
    arts = [a for a in artifacts.listing(t["id"]) if not artifacts.is_note(a["name"])]
    return graph.deliverables(arts, t.get("goal") or "")


@app.get("/api/records")
async def records(q: str = "", status: str = "", before: int = 0, limit: int = 40):
    """「记录」页：做完的事，一件事一行。status：空 全部 / done 交付了 / failed 没做成（含取消）；before 往前翻。"""
    where = ["parent_id IS NULL", "COALESCE(kind,'')!='trial'", "status IN ('done','failed','cancelled')"]
    args: list = []
    if status == "done":
        where.append("status='done'")
    elif status == "failed":
        where.append("status IN ('failed','cancelled')")
    if before:
        where.append("updated_at<?")
        args.append(before)
    words = q.strip()
    if words:
        like = f"%{words}%"
        wids = [c.id for c in registry.all_cards() if words in c.name]
        ph = ",".join("?" * len(wids))
        where.append("(title LIKE ? OR goal LIKE ? OR id IN (SELECT main_id FROM artifacts WHERE name LIKE ?)"
                     + (f" OR id IN (SELECT parent_id FROM tasks WHERE worker_id IN ({ph}))" if wids else "") + ")")
        args += [like, like, like] + wids
    rows = db.q(f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?",
                tuple(args) + (min(max(limit, 1), 100),))
    out = []
    for t in rows:
        kids = db.children(t["id"])
        files = _delivered(t)
        bad = t.get("verdict") and not t["verdict"].get("pass", True)
        out.append({
            "id": t["id"], "title": t["title"], "status": t["status"], "kind": t.get("kind") or "",
            "note": t.get("note") or "", "updated_at": t["updated_at"], "created_at": t["created_at"],
            "problems": len(t["verdict"].get("problems") or []) if bad else 0,
            "workers": list(dict.fromkeys(k["worker_id"] for k in kids if k.get("worker_id"))),
            "files": len(files),
            "first_file": next((a["name"] for a in files if not artifacts.is_note(a["name"])), files[0]["name"] if files else ""),
        })
    return out


@app.get("/api/tasks/{tid}")
async def task_detail(tid: str):
    t = db.task(tid) or _404()
    main = actions.main_of(t)
    ids = [main["id"]] + [k["id"] for k in db.children(main["id"])]
    ph = ",".join("?" * len(ids))
    main["children"] = db.children(main["id"])
    main["events"] = db.q(f"SELECT * FROM task_events WHERE task_id IN ({ph}) ORDER BY id DESC LIMIT 200", tuple(ids))
    main["decisions"] = db.q("SELECT * FROM decisions WHERE task_id=? ORDER BY id", (main["id"],))
    main["messages"] = db.q("SELECT * FROM messages WHERE task_id=? ORDER BY id", (main["id"],))
    main["memories"] = db.q("SELECT * FROM memories WHERE task_id=? ORDER BY id", (main["id"],))
    main["artifacts"] = artifacts.listing(main["id"])
    main["team"] = team.listing(main["id"])
    main["approvals"] = db.q(f"SELECT * FROM approvals WHERE status='pending' AND task_id IN ({ph}) ORDER BY id", tuple(ids))
    return main


@app.post("/api/tasks/{tid}/cancel")
async def cancel(tid: str):
    t = db.task(tid) or _404()
    r = runner.get()
    if t["parent_id"]:
        await r.cancel(tid)
        db.update("tasks", tid, status="cancelled", note="已取消")
        events.event(tid, "cancelled", "已取消")
        events.task_changed(tid)
        await r.check_parent(t["parent_id"])
    else:
        await r.cancel_main(tid)
        db.update("tasks", tid, status="cancelled", note="已取消")
        events.event(tid, "cancelled", "整件事已取消")
        events.task_changed(tid)
    return db.task(tid)


@app.post("/api/tasks/{tid}/assign")
async def assign(tid: str, body: Assign):
    t = db.task(tid) or _404()
    if not t["parent_id"]:
        raise HTTPException(400, "只能改派子任务")
    if not registry.get(body.worker_id):
        _404("队员")
    if t["status"] == "running" and t["progress"] > classify.confirm_above() and not body.confirm:
        return {"need_confirm": True, "progress": t["progress"]}
    return await actions.reassign(tid, body.worker_id)


@app.get("/api/tasks/{tid}/artifact")
async def artifact(tid: str):
    t = db.task(tid) or _404()
    if not t["artifact"] or not Path(t["artifact"]).exists():
        _404("文档")
    return FileResponse(t["artifact"], media_type="text/plain; charset=utf-8")


# ---------- 插话 ----------
async def interject_job(text: str, main: dict, *, shown: str | None = None, attachments: list | None = None) -> dict:
    events.message(main["id"], "user", shown or text, **({"attachments": attachments} if attachments else {}))
    d = await classify.classify(text, main["id"])
    target = db.task(d.target) if d.target else None
    dec_id = db.insert(
        "decisions", task_id=main["id"], utterance=text, intent=d.intent, target=d.target, reason=d.reason,
        cost=d.cost, confidence=round(d.confidence, 2), detail=d.detail, source=d.source,
        need_confirm=int(d.need_confirm), progress=target["progress"] if target else 0,
        status="pending_confirm" if d.need_confirm else "running", final_action=d.intent, created_at=db.now())
    row = db.q1("SELECT * FROM decisions WHERE id=?", (dec_id,))
    row["unsure"] = d.unsure
    events.publish("decision", row)
    events.message(main["id"], "decision", text, decision_id=dec_id, unsure=d.unsure)
    if not d.need_confirm:
        asyncio.create_task(actions.execute(dec_id))
    return {"decision_id": dec_id, **d.model_dump(),
            "action": "await_confirm" if d.need_confirm else actions.ACTION_NAME[d.intent]}


@app.post("/api/interject")
async def interject(body: Interject):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "说点什么")
    t = db.task(body.task_id) if body.task_id else None
    main = actions.main_of(t) if t else None
    if main is None or main["status"] in db.TERMINAL:
        m = create_main(text)
        return {"action": "new_task", "task_id": m["id"]}
    return await interject_job(text, main)


async def _chat(text: str, files: list[dict] | None = None) -> None:
    what = memory.explicit(text) if not files else None
    if what:
        row = memory.add(memory.guess_kind(what), what, source="manual")
        events.message(None, "assistant", f"记住了：{row['content']}" if row else "这句太短，没记", kind="memory",
                       memory_id=row["id"] if row else None)
        return
    renamed = memory.quick(text)
    answer = await front.reply(text, files or [])
    events.message(None, "assistant", answer, kind="chat")
    learned = [renamed] if renamed else await memory.learn_from_chat(text, answer)
    for row in learned:
        events.message(None, "assistant", f"记住了：{row['content']}", kind="memory", memory_id=row["id"])


@app.post("/api/say")
async def say(body: dict):
    """对话框的唯一入口：先分流，再走闲聊 / 新的活 / 插话。可带附件 id。"""
    from . import setup
    text, _ = setup.hide_secrets(str(body.get("text", "")).strip())  # key 一进来就打码：库里、模型、记忆都只见占位符
    files = uploads.many(body.get("attachments") or [])
    if not text and not files:
        raise HTTPException(400, "说点什么")
    if not text:
        text = "看看这些附件" if len(files) > 1 else f"看看这个{'图片' if files[0]['kind'] == 'image' else '文件'}"
    att = [uploads.public(u) for u in files]
    if text.startswith("/"):
        out = await _slash(text, files, att)
        if out:
            return out
    pinned = str(body.get("worker_id") or "").strip() or None
    if pinned and not registry.get(pinned):
        raise HTTPException(400, f"找不到队员：{pinned}")
    mention_id, clean_text = _extract_mention(text)
    if not pinned and mention_id:
        pinned = mention_id
        if not clean_text:
            raise HTTPException(400, f"@{registry.get(pinned).name} 后面写要做什么")
    if pinned:
        goal = await _with_attachments(clean_text if mention_id else text, files)
        m = create_main(goal, shown=text, attachments=att, worker_id=pinned)
        return {"kind": "new", "job": m["id"], "source": "mention", "worker_id": pinned}
    waiting = [j for j in db.main_tasks() if j["status"] == "running" and j.get("note") == "等你点头"]
    if waiting and not re.search(r"[？?]\s*$|吗[。]?$", text):  # 计划模式：拆好了等主人点头
        j = waiting[0]
        events.message(j["id"], "user", text, attachments=att)
        if GO.match(text.strip()):
            await runner.get().plan_go(j["id"])
            return {"kind": "go", "job": j["id"]}
        events.message(j["id"], "assistant", "好，按这个改，重新拆一下")
        await runner.get().plan_revise(j["id"], text)
        return {"kind": "revise", "job": j["id"]}
    # 若当前挂着「推翻重来吗？」待确认弹窗，主人直接发了文字回复：先按文字意图消解该弹窗，不再死挂在界面上
    pending_confirms = db.q("SELECT * FROM decisions WHERE status='pending_confirm' ORDER BY id DESC")
    if pending_confirms:
        t_clean = text.strip()
        confirm_go = re.match(r"^(?:推翻重来|推翻|重来|从头重来|从头来|重新来|确认推翻)[吧啊！!。.～~]*$", t_clean)
        confirm_keep = re.match(r"^(?:那你继续|继续|继续吧|接着做|先别推翻|别推翻|不推翻|先继续|照旧|不用推翻)[吧啊！!。.～~]*$", t_clean)
        if confirm_go:
            d0 = pending_confirms[0]
            events.message(d0["task_id"], "user", text, attachments=att)
            res = await confirm_decision(d0["id"])
            return {"kind": "confirm", "job": d0["task_id"], **res}
        # 无论主人说「那你继续吧」「不用了」还是发了其他话，先清掉过时的待确认弹窗
        for d in pending_confirms:
            db.update("decisions", d["id"], status="cancelled")
            events.publish("decision", {"id": d["id"], "task_id": d["task_id"], "status": "cancelled"})
        if confirm_keep:
            d0 = pending_confirms[0]
            events.message(d0["task_id"], "user", text, attachments=att)
            events.message(d0["task_id"], "assistant", "好，不推翻，接着把这件事做完")
            return {"kind": "keep", "job": d0["task_id"]}
    r = await front.route(text) if not files or len(text) > 6 else {"kind": "chat", "source": "rule"}
    if r["kind"] == "stop":
        live = [j for j in db.main_tasks() if j["status"] not in db.TERMINAL]
        j = db.task(r["job"]) if r.get("job") else (live[0] if live else None)
        if j and j["status"] not in db.TERMINAL:
            events.message(j["id"], "user", text, attachments=att)
            await cancel(j["id"])
            events.message(j["id"], "assistant", f"好，「{j['title']}」停下了")
            return {"kind": "stop", "job": j["id"], "source": r["source"]}
        events.message(None, "user", text, attachments=att)
        events.message(None, "assistant", "现在没有在做的事", kind="chat")
        return {"kind": "stop", "job": None, "source": r["source"]}
    if r["kind"] == "chat":
        events.message(None, "user", text, attachments=att)
        asyncio.create_task(_chat(text, files))
        return {"kind": "chat", "source": r["source"]}
    if r["kind"] == "schedule":
        events.message(None, "user", text, attachments=att)
        try:
            got = r.get("auto") or {}
            try:
                got = {**got, "rule": automation.validate(got.get("rule") or {})}
                if not str(got.get("text") or "").strip():
                    raise ValueError("没写做什么")
            except (ValueError, TypeError):
                got = {**await automation.parse(text), "run_now": got.get("run_now")}
            a = automation.save({"name": got.get("name") or "", "rule": got["rule"], "text": got["text"]})
            events.message(None, "assistant", f"好，{a['when']}我{a['text']}", kind="automation",
                           automation_id=a["id"])
            if got.get("run_now"):
                await automation.fire(a["id"], lambda t, shown: create_main(t, shown=shown, kind="auto"))
            return {"kind": "schedule", "automation": a["id"], "source": r["source"]}
        except ValueError as e:
            events.message(None, "assistant", f"没建成自动化：{e}", kind="chat")
            return {"kind": "schedule", "automation": None, "source": r["source"]}
    if r["kind"] == "run_auto":
        events.message(None, "user", text, attachments=att)
        a = automation.get(r["automation"])
        job = await automation.fire(a["id"], lambda t, shown: create_main(t, shown=shown, kind="auto"))
        return {"kind": "run_auto", "automation": a["id"], "job": job["id"], "source": r["source"]}
    goal = await _with_attachments(r.get("goal") or text, files)
    if r["kind"] == "interject" and r.get("job"):
        main = db.task(r["job"])
        if main and main["status"] not in db.TERMINAL:
            out = await interject_job(goal, main, shown=text, attachments=att)
            return {"kind": "interject", "job": main["id"], **out}
    m = create_main(goal, shown=text, attachments=att)
    return {"kind": "new", "job": m["id"], "source": r["source"]}


GO = re.compile(r"^(?:好|行|可以|开工|开始|开干|干吧|没问题|就这样|就这么办|ok|OK|好的|可以开工|那就开工|开工吧)[吧啊呀！!。.～~]*$")


async def _slash(text: str, files: list[dict], att: list) -> dict | None:
    """输入框里的斜杠命令：/新事 /自动化 /记住 /停下 /计划。不认识的命令当普通的话处理。"""
    cmd, _, rest = text.partition(" ")
    rest = rest.strip()
    if cmd == "/清屏":
        from . import chatlog
        out = await chatlog.clear()
        return {"kind": "clear", **out}
    if cmd in ("/新事", "/计划"):
        if not rest:
            raise HTTPException(400, f"{cmd} 后面写要做什么")
        pinned, clean_rest = _extract_mention(rest)
        if pinned and not clean_rest:
            raise HTTPException(400, f"@{registry.get(pinned).name} 后面写要做什么")
        goal = await _with_attachments(clean_rest if pinned else rest, files)
        m = create_main(goal, shown=text, attachments=att, plan_first=cmd == "/计划", worker_id=pinned)
        return {"kind": "new", "job": m["id"], "source": "slash", **({"worker_id": pinned} if pinned else {})}
    if cmd == "/记住":
        if not rest:
            raise HTTPException(400, "/记住 后面写要记的事")
        events.message(None, "user", text)
        row = memory.add(memory.guess_kind(rest), rest, source="manual")
        events.message(None, "assistant", f"记住了：{row['content']}" if row else "这句太短，没记", kind="memory",
                       memory_id=row["id"] if row else None)
        return {"kind": "memory", "memory": row["id"] if row else None}
    if cmd == "/停下":
        live = [j for j in db.main_tasks() if j["status"] not in db.TERMINAL]
        events.message(None, "user", text)
        if not live:
            events.message(None, "assistant", "现在没有在做的事", kind="chat")
            return {"kind": "stop", "job": None}
        j = (front.pick_job(rest, live) if rest else None) or live[0]
        await cancel(j["id"])
        events.message(j["id"], "assistant", f"好，「{j['title']}」停下了")
        return {"kind": "stop", "job": j["id"]}
    if cmd == "/自动化":
        if not rest:
            raise HTTPException(400, "/自动化 后面写什么时候做什么，比如每个工作日 9 点整理邮件")
        events.message(None, "user", text)
        try:
            got = await automation.parse(rest)
            a = automation.save({"name": got.get("name") or "", "rule": got["rule"], "text": got["text"]})
        except ValueError as e:
            events.message(None, "assistant", f"没建成自动化：{e}", kind="chat")
            return {"kind": "schedule", "automation": None, "source": "slash"}
        events.message(None, "assistant", f"好，{a['when']}我{a['text']}", kind="automation", automation_id=a["id"])
        return {"kind": "schedule", "automation": a["id"], "source": "slash"}
    return None


async def _with_attachments(text: str, files: list[dict]) -> str:
    """派活时把附件变成文字拼进目标：文件给路径和摘录，图片先读成文字。"""
    if not files:
        return text
    parts = [text]
    block = uploads.text_block(files, 3000)
    if block:
        parts.append(block)
    imgs = [u for u in files if u["kind"] == "image"]
    if imgs:
        desc = await uploads.describe(imgs, text)
        names = "、".join(f"{u['name']}（{uploads.rel(u)}）" for u in imgs)
        parts.append(f"附图 {names}：\n{desc}" if desc else f"附图 {names}：当前模型看不了图，只能按文件处理")
    return "\n\n".join(parts)


@app.get("/api/state")
async def state():
    """对话页一次拿齐：全部对话、最近的事（带子任务）、判断、待确认、产物。"""
    jobs = db.main_tasks()[:40]
    for j in jobs:
        j["children"] = db.children(j["id"])
        j["team"] = team.listing(j["id"], 3) if len(j["children"]) > 1 else []
    ids = [j["id"] for j in jobs]
    ph = ",".join("?" * len(ids)) or "''"
    return {
        "jobs": jobs,
        "messages": list(reversed(db.q("SELECT * FROM messages ORDER BY id DESC LIMIT 400"))),
        "decisions": db.q(f"SELECT * FROM decisions WHERE task_id IN ({ph}) ORDER BY id", tuple(ids)),
        "approvals": db.q("SELECT * FROM approvals WHERE status='pending' ORDER BY id"),
        "artifacts": [a for a in artifacts.listing() if a["main_id"] in ids or not a["main_id"]],
        "workspace": str(settings.workspace()),
        "name": memory.assistant_name(),
        "avatar": "/api/soul/avatar" if (db.DATA_DIR / "avatar.img").exists() else "",
        "goals": goals.listing("active"),
        "connectors": sum(1 for c in t_mcp.load().values() if c.get("enabled", True)),
    }


@app.get("/api/starters")
async def list_starters():
    return starters.listing()


# ---------- 长期跟进（Goals） ----------
@app.get("/api/goals")
async def list_goals(status: str = ""):
    return goals.listing(status)


@app.post("/api/goals")
async def new_goal(body: dict):
    try:
        return goals.save(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/goals/{gid}")
async def edit_goal(gid: str, body: dict):
    try:
        if set(body) == {"status"}:
            return goals.set_status(gid, str(body["status"]))
        return goals.save(body, gid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/goals/{gid}")
async def delete_goal(gid: str):
    goals.remove(gid)
    return {"ok": True}


@app.post("/api/goals/{gid}/review")
async def review_goal(gid: str):
    try:
        job = await goals.review(gid, lambda text, shown, g: create_main(text, shown=shown, kind="goal", goal_id=g))
        return {"job": job["id"]}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


# ---------- 定时自动化 ----------
@app.get("/api/automations")
async def list_automations():
    return automation.listing()


@app.post("/api/automations/parse")
async def parse_automation(body: dict):
    try:
        return await automation.parse(str(body.get("text", "")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/automations")
async def new_automation(body: dict):
    try:
        return automation.save(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/automations/{aid}")
async def edit_automation(aid: int, body: dict):
    automation.get(aid) or _404("自动化")
    try:
        if set(body) == {"enabled"}:
            return automation.toggle(aid, bool(body["enabled"]))
        return automation.save(body, aid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/automations/{aid}")
async def delete_automation(aid: int):
    automation.remove(aid)
    return {"ok": True}


@app.post("/api/automations/{aid}/run")
async def run_automation(aid: int):
    automation.get(aid) or _404("自动化")
    job = await automation.fire(aid, lambda text, shown: create_main(text, shown=shown, kind="auto"))
    return {"job": job["id"]}


# ---------- 附件 ----------
@app.post("/api/uploads")
async def upload(file: UploadFile):
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "单个附件不超过 50 MB")
    return uploads.save(file.filename or "附件", data, file.content_type or "")


@app.get("/api/uploads/{uid}/raw")
async def upload_raw(uid: int):
    u = uploads.get(uid) or _404("附件")
    return FileResponse(u["path"], media_type=u["mime"] or None)


@app.post("/api/workspace/reveal")
async def reveal_workspace():
    ws = settings.workspace()
    ws.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(ws)])
    return {"ok": True, "path": str(ws)}


# ---------- 放行 ----------
@app.post("/api/approvals/{aid}")
async def decide_approval(aid: int, body: dict):
    row = db.q1("SELECT * FROM approvals WHERE id=?", (aid,)) or _404("请求")
    if row["status"] != "pending":
        raise HTTPException(409, "这条已经处理过了")
    choice = body.get("decision")
    if choice not in ("allow", "allow_task", "deny"):
        raise HTTPException(400, "decision 只能是 allow / allow_task / deny")
    from .tools.risk import ALWAYS_ASK
    if choice == "allow_task" and row["tool"] in ALWAYS_ASK:  # 发信这类每次都要问，不给「都允许」
        choice = "allow"
    ans = body.get("answer")
    if isinstance(ans, list):
        ans = "、".join(str(x).strip() for x in ans if str(x).strip())
    elif ans is not None:
        ans = str(ans).strip()
    db.update("approvals", aid, status="denied" if choice == "deny" else "allowed", decided_at=db.now(),
              **({"answer": ans} if ans is not None else {}))
    if choice == "allow_task" and row["main_id"]:
        main = db.task(row["main_id"]) or {}
        grants = list(main.get("grants") or [])
        from .tools.risk import COMPUTER, COMPUTER_GRANT
        group = COMPUTER if row["tool"] in COMPUTER else {row["tool"]}  # 电脑操作一组：点、打字、按键、打开应用一起放行
        key = COMPUTER_GRANT if row["tool"] in COMPUTER else row["tool"]
        if key not in grants:
            db.update("tasks", row["main_id"], grants=grants + [key])
        ph = ",".join("?" * len(group))
        for other in db.q(f"SELECT id FROM approvals WHERE main_id=? AND tool IN ({ph}) AND status='pending'",
                          (row["main_id"], *group)):
            db.update("approvals", other["id"], status="allowed", decided_at=db.now())
    events.event(row["task_id"], "approved" if choice != "deny" else "denied",
                 f"{'允许' if choice != 'deny' else '拒绝'}：{row['summary']}")
    events.publish("approval", {"id": aid, "status": choice})
    from . import worker_acp
    worker_acp.notify_approval(aid, choice)
    tasks = {row["task_id"]} | {r["task_id"] for r in db.q(
        "SELECT DISTINCT task_id FROM approvals WHERE main_id=? AND decided_at>=?", (row["main_id"], db.now() - 2000))}
    for tid in tasks:
        await runner.get().resume(tid)
    return {"ok": True}


# ---------- 产物 ----------
@app.get("/api/artifacts")
async def list_artifacts(job: str | None = None):
    return artifacts.listing(job)


def _artifact(aid: int) -> dict:
    a = db.q1("SELECT * FROM artifacts WHERE id=?", (aid,)) or _404("文件")
    if not Path(a["path"]).exists():
        _404("文件")
    return a


TEXT_SUFFIX = {".js", ".mjs", ".ts", ".tsx", ".jsx", ".css", ".py", ".sh", ".yaml", ".yml", ".toml", ".xml", ".svg",
               ".log", ".ini", ".sql", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".swift", ".kt", ".rb", ".php", ".vue"}


@app.get("/api/artifacts/{aid}/content")
async def artifact_content(aid: int):
    a = _artifact(aid)
    suf = Path(a["path"]).suffix.lower()
    if (a["kind"] not in ("doc", "sheet") and suf not in TEXT_SUFFIX) or suf in (".docx", ".pdf", ".xlsx", ".xls"):
        raise HTTPException(415, "这类文件用系统里的应用打开")
    return FileResponse(a["path"], media_type="text/plain; charset=utf-8")


@app.get("/api/artifacts/{aid}/preview")
async def artifact_preview(aid: int):
    """Word / Excel 转成 HTML，右栏里直接看。"""
    from fastapi.responses import HTMLResponse
    from . import docview
    a = _artifact(aid)
    try:
        out = docview.render(Path(a["path"]))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"打不开：{type(e).__name__}") from e
    if out is None:
        raise HTTPException(415, "这类文件用系统里的应用打开")
    return HTMLResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/view/{tok}/{aid}/{rel:path}")
async def view_artifact(tok: str, aid: int, rel: str):
    """右栏里直接跑网页产物：令牌放在路径里，网页里相对路径引用的 js / css / 图片也带得上。
    只给这个产物所在文件夹里的文件。"""
    token = os.environ.get("SA_API_TOKEN", "")
    if token and not secrets.compare_digest(tok.encode(), token.encode()):
        raise HTTPException(401, "令牌不对")
    a = _artifact(aid)
    base = Path(a["path"]).parent.resolve()
    f = (base / (rel or Path(a["path"]).name)).resolve()
    if not f.is_relative_to(base) or not f.is_file():
        raise HTTPException(404, "文件不见了")
    return FileResponse(f, headers={"Cache-Control": "no-store"})


@app.post("/api/artifacts/{aid}/reveal")
async def reveal_artifact(aid: int, body: dict | None = None):
    a = _artifact(aid)
    if sys.platform == "darwin":
        args = ["open", a["path"]] if (body or {}).get("open") else ["open", "-R", a["path"]]
        subprocess.Popen(args)
    return {"ok": True, "path": a["path"]}


# ---------- 技能 ----------
@app.get("/api/skills")
async def list_skills():
    """技能库（内置 + 主人的）和导入的队员包里自带的；users 是能用它的队员。"""
    cards = registry.all_cards()
    use: dict[str, list[str]] = {}
    for c in cards:
        if c.enabled:
            for k in t_skills.for_worker(c, registry.prefs(c.id)["skills"]):
                use.setdefault(f"{k.source if k.source in ('builtin', 'global') else 'own'}:{k.id}", []).append(c.id)
    out = [{"id": s.id, "name": s.name, "description": s.description, "source": s.source, "has_script": s.has_script,
            "dir": s.dir, "users": use.get(f"{s.source}:{s.id}", [])} for s in t_skills.library()]
    for c in cards:
        for k in c.skills:
            out.append({"id": k.id, "name": k.name, "description": k.description, "source": c.id,
                        "owner": c.name, "has_script": k.has_script, "dir": k.dir, "users": [c.id]})
    return out


@app.post("/api/skills")
async def new_skill(body: dict):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "要有名称")
    s = t_skills.create(name, str(body.get("description", "")).strip() or name, str(body.get("body", "")),
                        str(body.get("id", "")))
    for wid in body.get("give") or []:
        _give(str(wid), s.id, True)
    return {"id": s.id, "name": s.name}


def _give(wid: str, sid: str, on: bool) -> bool:
    c = registry.get(wid)
    if not c:
        return False
    cur = t_skills.picked(wid, registry.prefs(wid)["skills"])
    nxt = [x for x in cur if x != sid] + ([sid] if on else [])
    return registry.set_prefs(wid, skills=nxt)


@app.post("/api/skills/install")
async def install_skill(body: dict):
    """从本机文件夹、zip 路径或 GitHub 链接装技能。"""
    try:
        names = await t_skills.install(str(body.get("source", "")), bool(body.get("replace")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"安装失败：{type(e).__name__}") from e
    return {"ok": True, "skills": names}


@app.post("/api/skills/import")
async def import_skill(file: UploadFile):
    tmp = Path(tempfile.mkdtemp(dir=db.DATA_DIR))
    try:
        zp = tmp / "skill.zip"
        with open(zp, "wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            return {"ok": True, "skills": t_skills._from_zip(zp, False)}
        except ValueError as e:
            return {"ok": False, "errors": [str(e)]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/api/skills/{sid}/copy")
async def copy_skill(sid: str):
    try:
        s = t_skills.copy_builtin(sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": s.id, "name": s.name}


@app.get("/api/skills/doc")
async def skill_doc(source: str, id: str):  # noqa: A002
    doc = t_skills.read(source, id)
    if doc is None:
        _404("技能")
    return {"doc": doc, "editable": source == "global"}


@app.put("/api/skills/{sid}")
async def save_skill(sid: str, body: dict):
    try:
        s = t_skills.write(sid, str(body.get("doc", "")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": s.id, "name": s.name}


@app.post("/api/skills/draft")
async def draft_skill(body: dict):
    if not llm.configured():
        raise HTTPException(400, "先配置模型服务")
    try:
        return await author.draft_skill(str(body.get("text", "")))
    except Exception as e:
        raise HTTPException(502, f"起草失败：{type(e).__name__}") from e


@app.delete("/api/skills/{sid}")
async def delete_skill(sid: str):
    if not t_skills.remove(sid):
        raise HTTPException(400, "内置技能删不掉，可以在队员那里不勾")
    return {"ok": True}


# ---------- 连接器 ----------
@app.post("/api/chat/clear")
async def chat_clear():
    """清屏：界面从空白开始，记录不删；这一段压成前情，Yip 照样记得。"""
    from . import chatlog
    return await chatlog.clear()


@app.get("/api/history")
async def history(q: str = "", kind: str = "", date: str = "", before: int = 0):
    from . import chatlog
    try:
        return chatlog.search(q.strip(), kind, date, before)
    except ValueError as e:
        raise HTTPException(400, "日期要写成 2026-11-24") from e


@app.get("/api/history/dates")
async def history_dates(month: str):
    from . import chatlog
    try:
        return chatlog.dates(month)
    except ValueError as e:
        raise HTTPException(400, "月份要写成 2026-11") from e


@app.get("/api/browser")
async def browser_state():
    """右栏「浏览器」：队员现在在哪一页。"""
    from .tools import browser
    return browser.B.state()


@app.get("/api/browser/shot")
async def browser_shot():
    from fastapi.responses import Response
    from .tools import browser
    img = await browser.B.shot()
    if not img:
        raise HTTPException(404, "浏览器没开")
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/browser/show")
async def browser_show(body: dict | None = None):
    """主人要自己动手（show=true）：弹出可见窗口；交还给 Yip（show=false）：收回后台。"""
    from .tools import browser
    await browser.B.ensure(headed=bool((body or {}).get("show", True)))
    if browser.B.active:
        await browser.B.page(browser.B.active, notify_open=False)
    return browser.B.state()


@app.post("/api/browser/open")
async def browser_open_url(body: dict):
    """主人在界面上输入或点一个网址，在内置浏览器里打开。"""
    from .tools import ToolError, browser
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(400, "缺少网址")
    try:
        return await browser.B.navigate(url, key=body.get("key"))
    except ToolError as e:
        raise HTTPException(502, str(e)) from e


@app.websocket("/api/browser/ws")
async def browser_ws(ws: WebSocket):
    """实时浏览器画面流 + 鼠标键盘双向操控。"""
    from .tools import ToolError, browser
    await ws.accept()
    q = await browser.B.subscribe()

    async def sender():
        await ws.send_json({"type": "state", "data": browser.B.state()})
        await browser.B.push_frame(q)
        while True:
            msg = await q.get()
            await ws.send_json(msg)

    async def receiver():
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            try:
                if t == "navigate":
                    await browser.B.navigate(str(msg.get("url", "")), key=msg.get("key"))
                elif t in ("back", "forward", "reload"):
                    await browser.B.nav_action(t)
                elif t == "new_tab":
                    await browser.B.new_tab(str(msg.get("url", "")))
                elif t == "switch_tab":
                    await browser.B.switch_tab(str(msg.get("key", "")))
                elif t == "close_tab":
                    await browser.B.close_tab(str(msg.get("key", "")))
                elif t == "resize":
                    await browser.B.resize(int(msg.get("w", 1280)), int(msg.get("h", 800)))
                elif t == "mouse":
                    await browser.B.mouse(
                        str(msg.get("kind", "click")), float(msg.get("x", 0)), float(msg.get("y", 0)),
                        button=str(msg.get("button", "left")),
                        delta_x=float(msg.get("dx", 0)), delta_y=float(msg.get("dy", 0)),
                    )
                elif t == "key":
                    await browser.B.keyboard("press", key=str(msg.get("key", "")))
                elif t == "text":
                    await browser.B.keyboard("text", text=str(msg.get("text", "")))
                elif t == "stop":
                    browser.B.stop_ai()
            except ToolError as e:
                await ws.send_json({"type": "error", "message": str(e)})
            except Exception as e:  # noqa: BLE001
                await ws.send_json({"type": "error", "message": f"操作失败：{type(e).__name__}"})

    send_task = asyncio.create_task(sender())
    recv_task = asyncio.create_task(receiver())
    try:
        _, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        recv_task.cancel()
        await browser.B.unsubscribe(q)


@app.get("/api/mcp")
async def list_mcp():
    return t_mcp.status()


@app.get("/api/mcp/presets")
async def mcp_presets_list():
    installed = {c.get("preset") for c in t_mcp.load().values()}
    return [p | {"added": p["id"] in installed} for p in mcp_presets.public()]


@app.post("/api/mcp/presets/{pid}")
async def mcp_from_preset(pid: str, body: dict):
    if pid not in mcp_presets.BY_ID:
        _404("预置")
    try:
        cfg = mcp_presets.build(pid, body.get("values") or {})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    name = str(body.get("name") or mcp_presets.BY_ID[pid]["name"]).strip()
    servers = t_mcp.load()
    servers[name] = cfg
    t_mcp.save(servers)
    await t_mcp.disconnect(name)
    return {"ok": True, "name": name, "test": await t_mcp.test(name, cfg) if body.get("test") else None}


@app.get("/api/mcp/{name}/tools")
async def mcp_tools(name: str):
    cfg = t_mcp.load().get(name) or _404("连接器")
    return await t_mcp.test(name, cfg)


@app.put("/api/mcp/{name}")
async def save_mcp(name: str, body: dict):
    cfg = body.get("config") if isinstance(body.get("config"), dict) else body
    if not (cfg.get("command") or cfg.get("url")):
        raise HTTPException(400, "要填启动命令或服务地址")
    servers = t_mcp.load()
    servers[name] = cfg
    t_mcp.save(servers)
    await t_mcp.disconnect(name)
    return {"ok": True}


@app.post("/api/mcp/import")
async def import_mcp(body: dict):
    """粘贴 Claude Desktop / Cursor 的 {"mcpServers": {...}} 或直接 {...}。"""
    raw = body.get("json", "")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        raise HTTPException(400, "不是合法的 JSON") from None
    servers = data.get("mcpServers", data) if isinstance(data, dict) else {}
    good = {k: v for k, v in servers.items() if isinstance(v, dict) and (v.get("command") or v.get("url"))}
    if not good:
        raise HTTPException(400, "没找到能用的连接器配置")
    cur = t_mcp.load()
    cur.update(good)
    t_mcp.save(cur)
    return {"ok": True, "added": list(good)}


@app.delete("/api/mcp/{name}")
async def delete_mcp(name: str):
    cur = t_mcp.load()
    cur.pop(name, None)
    t_mcp.save(cur)
    await t_mcp.disconnect(name)
    return {"ok": True}


@app.post("/api/mcp/test")
async def test_mcp(body: dict):
    name = str(body.get("name") or "test")
    cfg = body.get("config") or t_mcp.load().get(name)
    if not cfg:
        raise HTTPException(404, "没有这个连接器")
    return await t_mcp.test(name, cfg)


@app.post("/api/decisions/{did}/override")
async def override(did: int, body: Override):
    dec = db.q1("SELECT * FROM decisions WHERE id=?", (did,)) or _404("判断")
    main = db.task(dec["task_id"]) if dec["task_id"] else None
    if dec["status"] != "pending_confirm":  # 只有最近一句、事情还在做时改判断才有意义；旧判断重放会把做完的事推倒重来
        if not main or main["status"] in db.TERMINAL:
            raise HTTPException(409, "这件事已经结束了，改判断不会生效；有新要求直接说")
        if db.q1("SELECT id FROM decisions WHERE task_id=? AND id>? LIMIT 1", (dec["task_id"], did)):
            raise HTTPException(409, "后来又说过话了，只能改最近一句的判断")
    if dec["status"] == "running":
        await asyncio.sleep(0.3)
    row = await actions.override(did, body.intent)
    return {"ok": True, "decision_id": did, "intent": body.intent, "action": actions.ACTION_NAME[body.intent],
            "decision": row}


@app.post("/api/decisions/{did}/confirm")
async def confirm(did: int):
    dec = db.q1("SELECT * FROM decisions WHERE id=?", (did,)) or _404("判断")
    if dec["status"] != "pending_confirm":
        raise HTTPException(409, "这条判断不需要确认")
    db.update("decisions", did, status="running")
    await actions.execute(did, "overturn")
    return {"ok": True, "decision": db.q1("SELECT * FROM decisions WHERE id=?", (did,))}


@app.get("/api/stats")
async def stats():
    rows = db.q("SELECT intent, final_action, overridden FROM decisions WHERE status != 'pending_confirm'")
    judged = len(rows)
    kept = sum(1 for r in rows if not r["overridden"])
    return {"judged": judged, "kept": kept, "overridden": judged - kept,
            "accuracy": round(kept / judged, 3) if judged else None}


# ---------- 记忆 ----------
@app.get("/api/memory")
async def list_memory():
    return memory.listing()


@app.post("/api/memory")
async def add_memory(body: dict):
    row = memory.add(str(body.get("kind") or "preference"), str(body.get("content") or ""), source="manual")
    if not row:
        raise HTTPException(400, "写一句完整的话")
    return row


@app.post("/api/memory/import")
async def import_memory(body: dict):
    rows = await memory.import_markdown(str(body.get("text") or ""))
    if not rows:
        raise HTTPException(400, "没提取出可记的内容，贴几行个人资料或偏好试试")
    return {"added": len(rows), "items": rows}


@app.post("/api/soul/avatar")
async def upload_soul_avatar(file: UploadFile):
    data = await file.read()
    if not data or len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "头像图片不能超过 5 MB")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "只能传图片文件")
    dest = db.DATA_DIR / "avatar.img"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    events.publish("settings", {"avatar": "/api/soul/avatar"})
    return {"ok": True, "url": "/api/soul/avatar"}


@app.get("/api/soul/avatar")
async def get_soul_avatar():
    dest = db.DATA_DIR / "avatar.img"
    if not dest.exists():
        _404("头像")
    return FileResponse(dest, headers={"Cache-Control": "no-store"})


@app.delete("/api/soul/avatar")
async def reset_soul_avatar():
    dest = db.DATA_DIR / "avatar.img"
    if dest.exists():
        dest.unlink()
    events.publish("settings", {"avatar": ""})
    return {"ok": True}


@app.put("/api/memory/{mid}")
async def edit_memory(mid: int, body: dict):
    return memory.update(mid, kind=body.get("kind"), content=body.get("content"), confirmed=body.get("confirmed")) or _404("记忆")


@app.delete("/api/memory/{mid}")
async def delete_memory(mid: int):
    memory.remove(mid)
    return {"ok": True}

@app.post("/api/memories/{mid}/confirm")
async def confirm_memory(mid: int):
    db.update("memories", mid, confirmed=1)
    row = db.q1("SELECT * FROM memories WHERE id=?", (mid,)) or _404("记忆")
    events.publish("memory", row)
    return row


@app.delete("/api/memories/{mid}")
async def drop_memory(mid: int):
    db.ex("DELETE FROM memories WHERE id=?", (mid,))
    return {"ok": True}


# ---------- 队员 ----------
@app.get("/api/workers")
async def workers():
    return [_member(c) for c in registry.all_cards()]


def _member(c) -> dict:
    p = registry.prefs(c.id)
    mine = t_skills.for_worker(c, p["skills"])
    return {**c.public(), "avatar": p["avatar"], "global_skills": t_skills.picked(c.id, p["skills"]),
            "skills": [{"id": k.id, "name": k.name, "description": k.description, "has_script": k.has_script,
                        "source": k.source} for k in mine], "connectors": p["connectors"]}


@app.put("/api/workers/{wid}/prefs")
async def worker_prefs(wid: str, body: dict):
    """avatar：预置头像 id；global_skills：从技能库勾的技能 id；connectors：null 表示全给，列表表示只给勾选的。"""
    kw = {}
    if "avatar" in body:
        kw["avatar"] = str(body["avatar"] or "")
    if "global_skills" in body:
        kw["skills"] = body["global_skills"]
    if "connectors" in body:
        kw["connectors"] = body["connectors"]
    if not registry.set_prefs(wid, **kw):
        _404("队员")
    return _member(registry.get(wid))


@app.get("/api/workers/{wid}")
async def worker_detail(wid: str):
    c = registry.get(wid) or _404("队员")
    return {**_member(c), "prompt": c.prompt, "form": author.to_form(c),
            "skill_docs": [{"id": k.id, "name": k.name, "description": k.description, "doc": k.doc, "source": k.source}
                           for k in t_skills.for_worker(c, registry.prefs(c.id)["skills"])]}


@app.put("/api/workers/{wid}")
async def update_worker(wid: str, form: dict):
    rep, card = author.update(wid, form)
    return {"report": rep.dict(), "worker": _member(card) if card else None}


@app.post("/api/workers/{wid}/try")
async def try_worker(wid: str, body: dict):
    """试一下：建一件只派给这个队员的试用活，不经 Yip 拆活、不进对话和记忆。"""
    card = registry.get(wid) or _404("队员")
    text = str(body.get("text") or "").strip() or author.sample(card)
    mid, kid = db.new_id(), db.new_id()
    now = db.now()
    db.insert("tasks", id=mid, parent_id=None, label="", title=f"试用 · {card.name}", goal=text, constraints=[],
              status="running", note="试用中", thread_id=f"main:{mid}", kind="trial", created_at=now, updated_at=now)
    db.insert("tasks", id=kid, parent_id=mid, label="A", title=text[:16], goal=text, constraints=[], status="pending",
              worker_id=wid, assign_reason="试用", thread_id=f"task:{kid}", created_at=now, updated_at=now)
    events.event(mid, "created", f"试用 {card.name}")
    await runner.get().start(kid)
    return {"job": mid, "task": kid}


@app.delete("/api/trials/{mid}")
async def delete_trial(mid: str):
    t = db.task(mid) or _404("试用")
    if t.get("kind") != "trial":
        raise HTTPException(400, "只能删试用")
    await runner.get().cancel_main(mid)
    for k in db.children(mid):
        db.ex("DELETE FROM task_events WHERE task_id=?", (k["id"],))
        db.ex("DELETE FROM approvals WHERE task_id=?", (k["id"],))
    db.ex("DELETE FROM artifacts WHERE main_id=?", (mid,))
    db.ex("DELETE FROM tasks WHERE id=? OR parent_id=?", (mid, mid))
    events.publish("task", {"id": mid, "deleted": True})
    return {"ok": True}


@app.post("/api/workers/{wid}/copy")
async def copy_worker(wid: str):
    rep, card = author.copy(wid)
    if rep is None:
        _404("队员")
    return {"report": rep.dict(), "worker": _member(card) if card else None}


@app.post("/api/workers")
async def save_worker(form: dict):
    rep, card = author.save(form)
    return {"report": rep.dict(), "worker": _member(card) if card else None}


@app.post("/api/workers/draft")
async def draft_worker(body: dict):
    if not llm.configured():
        raise HTTPException(400, "先配置模型服务")
    try:
        return await author.draft(str(body.get("text", "")))
    except Exception as e:
        raise HTTPException(502, f"起草失败：{type(e).__name__}") from e


@app.post("/api/workers/import")
async def import_worker(file: UploadFile):
    tmp_dir = db.DATA_DIR / "uploads" / uuid.uuid4().hex[:10]
    tmp_dir.mkdir(parents=True)
    zp = tmp_dir / "pkg.zip"
    try:
        with open(zp, "wb") as out:
            shutil.copyfileobj(file.file, out)
        rep, card = author.import_zip(zp)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"report": rep.dict(), "worker": card.public() if card else None}


@app.post("/api/workers/{wid}/enable")
async def enable_worker(wid: str, body: dict | None = None):
    on = True if body is None else bool(body.get("enabled", True))
    if not registry.set_enabled(wid, on):
        raise HTTPException(400, "内置队员不能停用")
    return registry.get(wid).public()


@app.delete("/api/workers/{wid}")
async def delete_worker(wid: str):
    if not registry.remove(wid):
        raise HTTPException(400, "内置队员不能删除")
    return {"ok": True}


# ---------- 外部队员（第三方本地 Agent） ----------
@app.get("/api/external-agents")
async def list_external_agents():
    from . import external
    return external.list_all()


@app.put("/api/external-agents/{aid}")
async def update_external_agent(aid: str, body: dict):
    from . import external
    try:
        item = external.update(aid, body)
    except KeyError:
        _404("外部队员")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    registry.reload()
    return item


@app.post("/api/external-agents/{aid}/probe")
async def probe_external_agent(aid: str, body: dict | None = None):
    from . import external
    if aid not in external.BY_ID:
        _404("外部队员")
    cmd = str((body or {}).get("command") or external.get_entry(aid)["command"])
    return external.probe(aid, cmd)


@app.post("/api/external-agents/{aid}/check")
async def check_external_agent(aid: str, body: dict | None = None):
    from . import external
    try:
        cmd = (body or {}).get("command") if isinstance(body, dict) else None
        return await external.check_connection(aid, custom_cmd=cmd)
    except KeyError:
        _404("外部队员")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/external-agents/{aid}/install")
async def install_external_agent(aid: str):
    from . import external
    try:
        res = await external.assist_install(aid)
        registry.reload()
        return res
    except KeyError:
        _404("外部队员")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------- 模型服务 ----------
@app.get("/api/settings")
async def get_settings():
    return {**settings.public(), "status": llm.status()}


@app.get("/api/mail/presets")
async def mail_presets():
    return [{"id": k, **v} for k, v in t_mail.PRESETS.items()]


@app.post("/api/mail/test")
async def mail_test(body: dict | None = None):
    c = {**t_mail.config(), **{k: v for k, v in (body or {}).items() if v and v != settings.MASK}}
    if not (c.get("address") and c.get("password") and c.get("imap_host")):
        return {"ok": False, "error": "邮箱、授权码、收信服务器都要填"}
    return await t_mail.test(c)


@app.get("/api/settings/defaults")
async def settings_defaults():
    return {"yip": settings.YIP_DEFAULTS}


@app.put("/api/settings")
async def put_settings(body: dict):
    s = settings.save(body)
    llm.STATUS.update(ok=None, error="")
    return {**s, "status": llm.status()}


@app.post("/api/settings/test")
async def test_settings(body: dict | None = None):
    try:
        await llm.chat([{"role": "user", "content": "回复 ok"}], json_mode=False, timeout=15,
                       override=settings.override(body or {}))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"连不上：{llm.human_reason(str(e))}", "detail": str(e)[:300]}


# ---------- SSE ----------
@app.get("/api/stream")
async def stream():
    qu = events.subscribe()

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(qu.get(), 15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(qu)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


if WEB.is_dir():  # 冻结后两处都没有：页面由 Tauri 自己托管
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
