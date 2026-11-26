"""和 Yip 的那一条对话：清屏和聊天记录。

清屏只清界面，不删记录：在对话里落一个「清屏」标记，把这一段压成几句前情（聊了什么、定了什么、还有什么没完）记在标记上。
之后 Yip 聊天只带清屏后的话，再加最近几段的前情，所以清了屏她照样记得之前聊过什么。
聊天记录照微信：按关键词搜，按文件、图片、链接、日期筛。
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import db, events, llm

SUMMARY = """下面是主人和助理的一段对话。压成三五句「前情」，以后接着聊时用：聊了什么、定了什么、交了什么、还有什么没完。
只写事实，不写客套，不超过 150 字。
{text}"""
LINK = re.compile(r"https?://\S+")


def _meta(m: dict) -> dict:
    v = m.get("meta")
    return v if isinstance(v, dict) else (json.loads(v) if v else {})


def last_clear() -> dict | None:
    for m in db.q("SELECT * FROM messages ORDER BY id DESC LIMIT 2000"):
        if _meta(m).get("kind") == "clear":
            return m
    return None


def since_clear_id() -> int:
    m = last_clear()
    return m["id"] if m else 0


def recent(limit: int) -> list[dict]:
    """清屏后的对话（新的在前），给 Yip 聊天、判断一句话用。"""
    return db.q("SELECT role, content FROM messages WHERE role IN ('user','assistant') AND id>? ORDER BY id DESC LIMIT ?",
                (since_clear_id(), limit))


def earlier(n: int = 3) -> str:
    """最近几次清屏前的前情，拼进系统提示：清了屏也记得之前聊过什么。"""
    marks = [m for m in db.q("SELECT * FROM messages ORDER BY id DESC LIMIT 2000") if _meta(m).get("kind") == "clear"][:n]
    lines = [f"- {dt.datetime.fromtimestamp(m['created_at'] / 1000).strftime('%m-%d %H:%M')} 之前：{_meta(m).get('summary')}"
             for m in reversed(marks) if _meta(m).get("summary")]
    return ("## 之前聊过（主人清了屏，界面上看不到，但你要记得）\n" + "\n".join(lines)) if lines else ""


async def clear() -> dict:
    """清屏：这一段压成前情记在标记上。没说过话就不落标记。"""
    seg = db.q("SELECT role, content FROM messages WHERE role IN ('user','assistant') AND id>? ORDER BY id",
               (since_clear_id(),))
    if not seg:
        return {"ok": True, "summary": ""}
    text = "\n".join(f"{'主人' if m['role'] == 'user' else '助理'}：{m['content'][:300]}" for m in seg[-60:])
    summary = ""
    if llm.configured():
        try:
            summary = (await llm.chat([{"role": "user", "content": SUMMARY.format(text=text)}], fast=True,
                                      json_mode=False, timeout=25)).strip()[:300]
        except Exception:  # noqa: BLE001
            summary = ""
    if not summary:  # 模型没回：留主人最近说的几句
        summary = "；".join(m["content"][:40] for m in seg if m["role"] == "user")[-200:]
    row = events.message(None, "assistant", "清屏", kind="clear", summary=summary)
    return {"ok": True, "id": row["id"], "summary": summary}


def search(q: str = "", kind: str = "", date: str = "", before: int = 0, limit: int = 60) -> list[dict]:
    """聊天记录：q 关键词（说的话、文件名）；kind = file / image / link；date = YYYY-MM-DD；before = 翻页用的 id。"""
    where, args = ["role IN ('user','assistant')"], []
    if before:
        where.append("id<?")
        args.append(int(before))
    if date:
        day = dt.datetime.strptime(date, "%Y-%m-%d")
        where.append("created_at>=? AND created_at<?")
        args += [int(day.timestamp() * 1000), int((day + dt.timedelta(days=1)).timestamp() * 1000)]
    rows = db.q(f"SELECT * FROM messages WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 1500", tuple(args))
    arts = {a["id"]: a for a in db.q("SELECT id, name, kind, path, size, task_id, main_id FROM artifacts")}
    out = []
    for m in rows:
        meta = _meta(m)
        if meta.get("kind") == "clear":
            continue
        files = [arts[i] for i in ([meta.get("artifact_id")] if meta.get("artifact_id") else []) + list(meta.get("files") or [])
                 if i in arts]
        atts = list(meta.get("attachments") or [])
        images = [a for a in atts if a.get("kind") == "image"] + [f for f in files if f["name"].lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".gif"))]
        docs = [a for a in atts if a.get("kind") != "image"] + [f for f in files if f not in images]
        links = LINK.findall(m["content"] or "")
        if kind == "file" and not docs:
            continue
        if kind == "image" and not images:
            continue
        if kind == "link" and not links:
            continue
        if q and q.lower() not in (m["content"] or "").lower() and not any(q.lower() in (f.get("name") or "").lower()
                                                                          for f in docs + images):
            continue
        out.append({"id": m["id"], "role": m["role"], "content": m["content"], "created_at": m["created_at"],
                    "task_id": m["task_id"], "docs": docs, "images": images, "links": links})
        if len(out) >= limit:
            break
    return out


def dates(month: str) -> list[str]:
    """这个月哪几天说过话（日历上能点的日子）。month = YYYY-MM。"""
    start = dt.datetime.strptime(month + "-01", "%Y-%m-%d")
    end = (start + dt.timedelta(days=32)).replace(day=1)
    rows = db.q("SELECT created_at FROM messages WHERE role IN ('user','assistant') AND created_at>=? AND created_at<?",
                (int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
    return sorted({dt.datetime.fromtimestamp(r["created_at"] / 1000).strftime("%Y-%m-%d") for r in rows})
