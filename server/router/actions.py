"""ActionExecutor：把判断落成动作。不做判断。

- 补充 augment  → 约束写进目标任务（主任务则连同未完成的子任务），下一轮生效，不打断
- 追加 append   → 新建一个「排队中」的子任务 + 写 task_queue（seq 单调），目标跑完按 seq 接上
- 推翻 overturn → 取消当前 run，落 discarded，task_id 不变、run_id 变，带新目标重跑
"""
from __future__ import annotations

import re

from .. import db, events, runner
from ..workers import match as matcher
from ..workers import registry

ACTION_NAME = {"augment": "steering", "append": "enqueue", "overturn": "interrupt"}
INTENT_CN = {"augment": "补充", "append": "追加", "overturn": "推翻"}


def main_of(t: dict) -> dict:
    return db.task(t["parent_id"]) if t.get("parent_id") else t


def who(t: dict) -> str:
    return registry.part_name(t) if t.get("parent_id") else "整件事"


def short_title(text: str) -> str:
    t = re.sub(r"^(能不能|可不可以|请|补充一下|顺便|另外|还有|再加一块|加一块|再|也|帮我|把|查一下|看一下|看看)[：:，, ]*", "", text.strip())
    t = re.sub(r"^(也把|补充一下|顺便|另外|还有|再|也|帮我|把|查一下|看一下|看看|查查|改看|改查|查|看)[：:，, ]*", "", t)
    t = re.sub(r"(那边|那块)?也?(看一下|查一下|看看|查查|对比一下|比一下)", "", t)
    t = re.sub(r"(也|一下|吧|。|了)$", "", t)
    return (t or text)[:12]


def pct(p: float) -> str:
    return f"{round((p or 0) * 100)}%"


# ---------- 补充 ----------
async def augment(dec: dict, target: dict, constraint: str) -> dict:
    ids = [target["id"]]
    if not target.get("parent_id"):
        ids += [k["id"] for k in db.children(target["id"]) if k["status"] not in db.TERMINAL]
    if target.get("parent_id") and target["status"] == "done":
        # 已经做完的子任务没有「下一轮」了：按补做一件新事排队
        return await append(dec, target, f"按「{constraint}」补做「{target['title']}」")
    for tid in ids:
        t = db.task(tid)
        db.update("tasks", tid, constraints=list(t["constraints"] or []) + [constraint])
        events.task_changed(tid)
    events.event(target["id"], "constraint_added", f"收到补充：{constraint}", decision_id=dec["id"])
    events.message(main_of(target)["id"], "assistant",
                   "行，加进去了" + (f"，{registry.part_name(target)}那块下一轮按这个改" if target.get("parent_id") else "，下一轮按这个改"), act=dec["id"])
    return {"type": "augment", "tasks": ids, "constraint": constraint}


# ---------- 追加 ----------
async def append(dec: dict, target: dict, payload: str) -> dict:
    main = main_of(target)
    title = short_title(payload)
    m = (await matcher.match([{"title": title, "goal": payload}]))[0]
    sid = db.new_id()
    label = db.next_label(main["id"])
    wait_for = target["id"]
    target_live = target["status"] not in db.TERMINAL
    note = f"排队中 · 等{registry.part_name(target)}做完" if target.get("parent_id") else "排队中 · 等其他子任务跑完"
    db.insert("tasks", id=sid, parent_id=main["id"], label=label, title=title, goal=f"{main['goal']}——补一件：{payload}",
              constraints=list(main["constraints"] or []), status="waiting", steps=4, note=note,
              depends_on=wait_for, worker_id=m["worker_id"], assign_reason=m["reason"], thread_id=f"task:{sid}",
              created_at=db.now(), updated_at=db.now())
    qid = db.insert("task_queue", task_id=wait_for, payload={"subtask_id": sid, "text": payload, "decision_id": dec["id"]},
                    seq=db.next_seq())
    card = registry.get(m["worker_id"])
    events.event(sid, "queued", f"排队：{payload}", decision_id=dec["id"])
    events.task_changed(sid)
    r = runner.get()
    if target.get("parent_id") and not target_live:
        await r.release_queue(wait_for)
        msg = f"好，{card.name if card else '同事'}这就去做「{title}」"
    else:
        msg = f"好，排上了，手上的做完就接着做「{title}」"
        if not target.get("parent_id"):
            await r.check_parent(main["id"])
    events.message(main["id"], "assistant", msg, act=dec["id"])
    return {"type": "append", "subtask": sid, "queue_id": qid}


# ---------- 推翻 ----------
async def overturn(dec: dict, target: dict, new_goal: str | None) -> dict:
    r = runner.get()
    lost = target["progress"]
    db.update("decisions", dec["id"], progress=lost)  # 等确认期间可能还在跑：按真正丢掉的算
    if not target.get("parent_id"):
        return await overturn_main(dec, target, new_goal)
    await r.cancel(target["id"])
    events.event(target["id"], "discarded", f"推翻，丢弃 {pct(lost)} 的进度", progress=lost, decision_id=dec["id"])
    main = main_of(target)
    if not new_goal:
        db.update("tasks", target["id"], status="cancelled", note="已推翻，不做了")
        db.ex("UPDATE task_queue SET consumed=1 WHERE task_id=?", (target["id"],))
        for w in db.q("SELECT * FROM tasks WHERE depends_on=? AND status='waiting'", (target["id"],)):
            await r.start(w["id"])
        events.task_changed(target["id"])
        events.message(main["id"], "assistant", f"好，{target['title']}那块不做了", act=dec["id"])
        await r.check_parent(main["id"])
        return {"type": "overturn", "task": target["id"]}
    title = short_title(new_goal)
    m = (await matcher.match([{"title": title, "goal": new_goal}]))[0]
    db.update("tasks", target["id"], goal=f"{main['goal']}——这一块改为：{new_goal}", title=title,
              constraints=list(main["constraints"] or []), progress=0, worker_id=m["worker_id"],
              assign_reason=m["reason"], status="pending", note="推翻后重跑")
    await r.start(target["id"])
    card = registry.get(m["worker_id"])
    events.message(main["id"], "assistant",
                   f"好，{registry.part_name(target)}那块重来，改做「{title}」",
                   act=dec["id"])
    return {"type": "overturn", "task": target["id"]}


async def overturn_main(dec: dict, main: dict, new_goal: str | None) -> dict:
    r = runner.get()
    lost = main["progress"]
    for k in db.children(main["id"]):
        if k["status"] not in db.TERMINAL:
            events.event(k["id"], "discarded", f"整件事推翻，丢弃 {pct(k['progress'])}", progress=k["progress"])
    await r.cancel_main(main["id"], delete_children=True)
    events.event(main["id"], "discarded", f"整件事推翻，丢弃 {pct(lost)} 的进度", progress=lost, decision_id=dec["id"])
    goal = new_goal or main["goal"]
    db.update("tasks", main["id"], goal=goal, title=short_title(goal) if new_goal else main["title"],
              constraints=[], progress=0, status="running", note="推翻后重新拆解", artifact=None, result=None)
    events.task_changed(main["id"])
    events.message(main["id"], "assistant",
                   f"好，之前的不要了，改做「{short_title(goal)}」" if new_goal else "好，从头重新来",
                   act=dec["id"])
    r.start_main(main["id"])
    return {"type": "overturn", "task": main["id"]}


# ---------- 入口 ----------
async def execute(dec_id: int, intent: str | None = None) -> None:
    dec = db.q1("SELECT * FROM decisions WHERE id=?", (dec_id,))
    intent = intent or dec["final_action"] or dec["intent"]
    target = db.task(dec["target"]) if dec["target"] else None
    if target is None:
        return
    text = dec["utterance"]
    same = intent == dec["intent"]
    try:
        if intent == "augment":
            undo = await augment(dec, target, (dec["detail"] if same else None) or text)
        elif intent == "append":
            undo = await append(dec, target, (dec["detail"] if same else None) or text)
        else:
            undo = await overturn(dec, target, (dec["detail"] if same else None) or (text if not same else None))
    except Exception as e:
        events.event(target["id"], "error", f"执行失败：{type(e).__name__}", detail=str(e)[:300])
        db.update("decisions", dec_id, status="failed")
        raise
    db.update("decisions", dec_id, status="done", final_action=intent, undo=undo)
    events.publish("decision", db.q1("SELECT * FROM decisions WHERE id=?", (dec_id,)))


async def undo(dec: dict) -> None:
    """改判断前撤掉上一个动作还能撤的部分。推翻已经丢了的进度撤不回来。"""
    u = dec.get("undo") or {}
    if u.get("type") == "augment":
        for tid in u.get("tasks", []):
            t = db.task(tid)
            if t:
                cons = list(t["constraints"] or [])
                if u["constraint"] in cons:
                    cons.remove(u["constraint"])
                    db.update("tasks", tid, constraints=cons)
                    events.task_changed(tid)
    elif u.get("type") == "append":
        st = db.task(u.get("subtask", ""))
        if st and st["status"] == "waiting":
            db.ex("DELETE FROM tasks WHERE id=?", (st["id"],))
            db.update("task_queue", u["queue_id"], consumed=1)
            events.publish("task_removed", {"id": st["id"], "parent_id": st["parent_id"]})
            events.refresh_parent(st["parent_id"])


async def override(dec_id: int, intent: str) -> dict:
    dec = db.q1("SELECT * FROM decisions WHERE id=?", (dec_id,))
    if dec["status"] == "done":
        await undo(dec)
    db.update("decisions", dec_id, overridden=1, final_action=intent, status="running")
    events.event(dec["target"] or "", "overridden", f"判断改成{INTENT_CN[intent]}", decision_id=dec_id)
    await execute(dec_id, intent)
    return db.q1("SELECT * FROM decisions WHERE id=?", (dec_id,))


async def reassign(task_id: str, worker_id: str) -> dict:
    t = db.task(task_id)
    card = registry.get(worker_id)
    r = runner.get()
    lost = t["progress"]
    if t["status"] == "running":
        await r.cancel(task_id)
        events.event(task_id, "discarded", f"改派，丢弃 {pct(lost)} 的进度", progress=lost)
    db.update("tasks", task_id, worker_id=worker_id, assign_reason="你改派的", progress=0)
    events.event(task_id, "reassigned", f"改派给{card.name}")
    if t["status"] in ("running", "done", "failed", "pending"):
        await r.start(task_id)
    events.task_changed(task_id)
    main = main_of(t)
    tail = f"，丢弃 {pct(lost)} 的进度" if lost and t["status"] == "running" else ""
    events.message(main["id"], "assistant", f"「{t['title']}」改派给{card.name}，从头做{tail}")
    return db.task(task_id)
