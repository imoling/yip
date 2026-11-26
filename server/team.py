"""小队频道：同一件事里的队员互相传话、退回问题。

- 留言（team_post）：发给全队或某个人，对方下一轮开始时就能看到（worker.load_context 插进对话）
- 退回（send_back）：测试、审查发现前面的人交的东西有问题，直接退回给他返工；他改完自动叫退回的人复查。
  同一个人最多被退回 MAX_BOUNCE 次，再有问题就写进交付，由 Yip 在「还差什么」里告诉主人
- 队友状态：每轮的系统提示里带上每个队友在做什么（label、谁、状态、在干嘛）
"""
from __future__ import annotations

from . import db, events
from .tools import READ, Ctx, Tool, ToolError, obj

MAX_BOUNCE = 2
SCHEMA = """CREATE TABLE IF NOT EXISTS team_msgs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, main_id TEXT NOT NULL, from_task TEXT, to_task TEXT,
  kind TEXT NOT NULL DEFAULT 'note', text TEXT NOT NULL, created_at INTEGER NOT NULL)"""
STATE = {"pending": "没开工", "waiting": "排队", "running": "在做", "done": "已交", "failed": "中断了", "cancelled": "取消了"}


def _ensure() -> None:
    db.ex(SCHEMA)


def _name(t: dict | None) -> str:
    if not t:
        return "Yip"
    from .workers import registry
    card = registry.get(t["worker_id"])
    return f"{t['label']}（{card.name if card else t['worker_id']}）"


def post(main_id: str, from_task: str | None, text: str, *, to_task: str | None = None, kind: str = "note") -> dict:
    _ensure()
    mid = db.insert("team_msgs", main_id=main_id, from_task=from_task, to_task=to_task, kind=kind, text=text,
                    created_at=db.now())
    src, dst = db.task(from_task) if from_task else None, db.task(to_task) if to_task else None
    line = f"{src['label'] if src else 'Yip'} → {dst['label'] if dst else '全队'}：{text}"
    events.event(main_id, "team_" + kind, line[:300], from_task=from_task, to_task=to_task)
    events.task_changed(main_id)
    return {"id": mid}


def listing(main_id: str, limit: int = 50) -> list[dict]:
    _ensure()
    rows = db.q("SELECT * FROM team_msgs WHERE main_id=? ORDER BY id DESC LIMIT ?", (main_id, limit))
    from .workers import registry
    kids = {k["id"]: k for k in db.children(main_id)}
    for r in rows:  # from / to 给界面看：队员名，不用 A、B 代号
        r["from"] = registry.part_name(kids[r["from_task"]]) if r["from_task"] in kids else "Yip"
        r["to"] = registry.part_name(kids[r["to_task"]]) if r["to_task"] in kids else None
    return list(reversed(rows))


def unread(main_id: str, me: str, after: int) -> tuple[str, int]:
    """给我的、我还没看过的留言，拼成一段话；返回（文本，最新 id）。"""
    _ensure()
    rows = db.q("SELECT * FROM team_msgs WHERE main_id=? AND id>? AND (from_task IS NULL OR from_task!=?) "
                "AND (to_task IS NULL OR to_task=?) ORDER BY id", (main_id, after, me, me))
    if not rows:
        return "", after
    lines = []
    for r in rows:
        who = _name(db.task(r["from_task"]) if r["from_task"] else None)
        tag = "退回给你的问题" if r["kind"] == "issue" else ("对你说" if r["to_task"] else "对全队说")
        lines.append(f"- {who}{tag}：{r['text']}")
    return "【小队频道，新消息】\n" + "\n".join(lines), rows[-1]["id"]


def roster(me: dict) -> str:
    """系统提示里的「小队」一节：队友是谁、在做什么。只有一个人干活时不写。"""
    if not me.get("parent_id"):
        return ""
    kids = db.children(me["parent_id"])
    if len(kids) < 2:
        return ""
    lines = []
    for k in kids:
        if k["id"] == me["id"]:
            continue
        doing = k["note"] if k["status"] in ("running", "waiting") and k.get("note") else ""
        lines.append(f"- {_name(k)}：{k['title']}——{STATE.get(k['status'], k['status'])}" + (f"，{doing}" if doing else ""))
    return ("## 小队（同一件事的队友，状态是实时的）\n" + "\n".join(lines) + "\n"
            "- 要让队友知道的事（改了接口、约定了文件名、发现了影响别人的坑）用 team_post 说一声，别闷着\n"
            "- 测试或审查发现前面的人交的东西有问题，用 send_back 退回给他改，不要自己大改别人的部分；"
            "退回后照常把你这一轮的结果交了，他改完会自动叫你复查")


def _kid(ctx: Ctx, label: str) -> dict:
    label = (label or "").strip().upper()[:2]
    k = next((x for x in db.children(ctx.main_id) if x["label"] == label), None)
    if not k:
        raise ToolError(f"这件事里没有 {label}，队友有：{'、'.join(x['label'] for x in db.children(ctx.main_id))}")
    if k["id"] == ctx.task_id:
        raise ToolError("不能发给自己")
    return k


async def team_post(ctx: Ctx, text: str, to: str = "") -> str:
    text = (text or "").strip()
    if not text:
        raise ToolError("说点什么")
    k = _kid(ctx, to) if to.strip() else None
    post(ctx.main_id, ctx.task_id, text[:1000], to_task=k["id"] if k else None)
    return f"已发给{k['label'] if k else '全队'}"


async def send_back(ctx: Ctx, to: str, problems: list[str]) -> str:
    probs = [str(p).strip() for p in problems or [] if str(p).strip()][:8]
    if not probs:
        raise ToolError("要写清退回的问题")
    k = _kid(ctx, to)
    if k["status"] in ("running", "pending", "waiting"):
        raise ToolError(f"{k['label']} 还在做，用 team_post 直接告诉他")
    if (k.get("bounces") or 0) >= MAX_BOUNCE:
        raise ToolError(f"{k['label']} 已经被退回 {MAX_BOUNCE} 次了，不再退；把剩下的问题写进你的交付，Yip 会告诉主人")
    me = db.task(ctx.task_id)
    db.update("tasks", k["id"], bounces=(k.get("bounces") or 0) + 1)
    post(ctx.main_id, ctx.task_id, "；".join(probs)[:1000], to_task=k["id"], kind="issue")
    msg = (f"{_name(me)} 检查了你交的东西，退回这些问题：\n" + "\n".join(f"- {p}" for p in probs)
           + "\n逐条改掉，改完自己检查一遍；最后一条回复说清改了什么。改完会自动交回给他复查。")
    from . import runner
    from .workers import registry
    await runner.get().reopen(k["id"], msg, return_to=ctx.task_id, note=f"被{registry.part_name(me)}退回：{probs[0][:40]}")
    card = registry.get(k["worker_id"])
    events.message(ctx.main_id, "assistant", f"测出问题：{probs[0][:80]}" + (f"等 {len(probs)} 处" if len(probs) > 1 else "")
                   + f"，退回给{card.name if card else k['label']}改了")
    return f"已退回给 {k['label']}，他改完会自动叫你复查；现在先把你这一轮的结果交了，交代里写上退回了什么"


TOOLS = [
    Tool("team_post", "在小队频道里给队友留言：全队都看得到，或者指定发给某个人（填他的编号，比如 B）。",
         obj({"text": {"type": "string"}, "to": {"type": "string", "description": "队友编号，留空是发给全队"}}, ["text"]),
         team_post, READ, lambda a: f"给{a.get('to') or '全队'}留言：{str(a.get('text', ''))[:40]}", "留言"),
    Tool("send_back", "把前面队友交的东西退回去返工：写清每个问题在哪个文件哪里、怎么复现。他改完会自动叫你复查。",
         obj({"to": {"type": "string", "description": "队友编号，比如 B"},
              "problems": {"type": "array", "items": {"type": "string"}}}),
         send_back, READ, lambda a: f"退回给 {a.get('to')}：{(a.get('problems') or [''])[0][:40]}", "退回"),
]
