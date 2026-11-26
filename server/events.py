"""EventBus：落库 + SSE 推送，不做业务。"""
from __future__ import annotations

import asyncio
import json

from . import db

_subs: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    qu: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subs.add(qu)
    return qu


def unsubscribe(qu: asyncio.Queue) -> None:
    _subs.discard(qu)


def publish(kind: str, data: dict) -> None:
    msg = json.dumps({"type": kind, "data": data}, ensure_ascii=False)
    for qu in list(_subs):
        try:
            qu.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def event(task_id: str, kind: str, text: str, **extra) -> dict:
    payload = {"text": text, **extra}
    eid = db.insert("task_events", task_id=task_id, type=kind, payload=payload, created_at=db.now())
    row = {"id": eid, "task_id": task_id, "type": kind, "payload": payload, "created_at": db.now()}
    publish("event", row)
    return row


def message(task_id: str | None, role: str, content: str, **meta) -> dict:
    mid = db.insert("messages", task_id=task_id, role=role, content=content, meta=meta or {}, created_at=db.now())
    row = {"id": mid, "task_id": task_id, "role": role, "content": content, "meta": meta, "created_at": db.now()}
    publish("message", row)
    return row


def task_changed(tid: str) -> None:
    t = db.task(tid)
    if t:
        publish("task", t)
        if t.get("parent_id"):
            refresh_parent(t["parent_id"])


def refresh_parent(pid: str) -> None:
    kids = [k for k in db.children(pid) if k["status"] != "cancelled"]
    if kids:
        prog = sum(k["progress"] for k in kids) / len(kids)
        p = db.task(pid)
        if p and p["status"] not in db.TERMINAL:
            db.update("tasks", pid, progress=round(prog, 3))
    p = db.task(pid)
    if p:
        publish("task", p)
