"""记忆：Yip 跨对话记得的事。

存在 memories 表（和每件事的沉淀共用一张表，task_id 为空表示不属于哪件事）。五类：
- preference 偏好：你希望怎么做（「给领导的材料先结论后论据」）
- fact 关于你：你是谁、在做什么（「在中科大读 MBA」）
- conclusion 结论：某件事定下了什么
- lesson 经验：哪条路试过不行
- state 进展：做到哪一步

怎么进来：你说「记住…」直接记；聊天里提到偏好或个人情况时由快模型提炼；每件事做完自动沉淀。
怎么用：聊天、拆活、队员干活、汇总时，按和当前这句话的相关度挑几条拼进提示词；偏好和关于你总是带上。
"""
from __future__ import annotations

import re

from . import db, events, llm, textsim

KINDS = {"persona": "关于 Yip", "preference": "偏好", "fact": "关于你", "conclusion": "结论", "lesson": "经验", "state": "进展"}
ALWAYS = ("persona", "preference", "fact")
REMEMBER = re.compile(r"^(?:请)?(?:帮我)?(?:记住|记一下|记下|记着|别忘了)[：:，,\s]*(.+)$", re.S)
HINT = re.compile(r"我是|我叫|我在|我们公司|我负责|我喜欢|我不喜欢|我习惯|以后|今后|每次|总是|不要再|别再|偏好|记得"
                  r"|你叫|叫你|你的名字|你就叫|你是|称呼|喊你|你说话|你的口吻|语气|别叫")
# 助理这边答应了改称呼 / 改做法，也值得提炼
AGREED = re.compile(r"叫我|称呼我|以后我会|我会记住|记住了|好的，我(会|以后)|没问题，以后")
NAME = re.compile(r"(?:叫|名字是|称呼)(?:我|你)?(?:为)?[「“\"']?([\u4e00-\u9fa5A-Za-z]{1,8})[」”\"']?")


def _norm(s: str) -> str:
    return re.sub(r"[\s，。,.！!？?；;、]", "", s or "")


def listing(kind: str | None = None) -> list[dict]:
    if kind:
        return db.q("SELECT * FROM memories WHERE kind=? ORDER BY COALESCE(updated_at, created_at) DESC", (kind,))
    return db.q("SELECT * FROM memories ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 500")


def add(kind: str, content: str, *, source: str = "manual", task_id: str = "", worker_id: str | None = None,
        confirmed: int = 1, goal_id: str | None = None) -> dict | None:
    """去重：内容几乎一样就更新时间，不新增。"""
    content = (content or "").strip().rstrip("。")
    if kind not in KINDS or len(content) < 2:
        return None
    key = _norm(content)
    for m in db.q("SELECT * FROM memories WHERE kind=?", (kind,)):
        old = _norm(m["content"])
        if old == key or (len(key) > 6 and (key in old or old in key)):
            kw = {"updated_at": db.now(), "content": content if len(content) > len(m["content"]) else m["content"]}
            if goal_id and not m.get("goal_id"):
                kw["goal_id"] = goal_id
            db.update("memories", m["id"], **kw)
            events.publish("memory", {"id": m["id"]})
            return db.q1("SELECT * FROM memories WHERE id=?", (m["id"],))
    mid = db.insert("memories", task_id=task_id or "", worker_id=worker_id, kind=kind, content=content,
                    confirmed=confirmed, source=source, goal_id=goal_id, created_at=db.now(), updated_at=db.now())
    row = db.q1("SELECT * FROM memories WHERE id=?", (mid,))
    events.publish("memory", row)
    return row


def update(mid: int, **kw) -> dict | None:
    cols = {k: v for k, v in kw.items() if k in ("kind", "content", "confirmed") and v is not None}
    if "kind" in cols and cols["kind"] not in KINDS:
        cols.pop("kind")
    if cols:
        db.update("memories", mid, updated_at=db.now(), **cols)
    return db.q1("SELECT * FROM memories WHERE id=?", (mid,))


def remove(mid: int) -> None:
    db.ex("DELETE FROM memories WHERE id=?", (mid,))
    events.publish("memory", {"id": mid, "deleted": True})


def relevant(text: str, n: int = 8) -> list[dict]:
    """偏好、关于你全带（最多 8 条）；其余按和 text 的字面重合挑。"""
    rows = listing()
    keep = [m for m in rows if m["kind"] in ALWAYS][:8]
    said = textsim.grams(text or "")
    rest = sorted((m for m in rows if m["kind"] not in ALWAYS),
                  key=lambda m: -len(said & textsim.grams(m["content"])))
    rest = [m for m in rest if said & textsim.grams(m["content"])][: max(0, n - len(keep)) or 3]
    used = keep + rest
    if used:
        ids = ",".join(str(m["id"]) for m in used)
        db.ex(f"UPDATE memories SET used_at=? WHERE id IN ({ids})", (db.now(),))
    return used


def block(text: str, n: int = 8) -> str:
    rows = relevant(text, n)
    if not rows:
        return ""
    lines = "\n".join(f"- [{KINDS[m['kind']]}] {m['content']}" for m in rows)
    return f"## 你记得的事（主人教过你的，照着做；和这次要求冲突时以这次为准）\n{lines}"


def explicit(text: str) -> str | None:
    m = REMEMBER.match(text.strip())
    return m.group(1).strip() if m else None


def guess_kind(content: str) -> str:
    if re.search(r"^你|叫你|你的名字|你说话|Yip", content):
        return "persona"
    return "fact" if re.search(r"^我(是|叫|在|们|负责)|我的", content) else "preference"


RENAME = re.compile(r"^(?:那)?(?:以后)?(?:你就?叫|叫你|你的名字(?:就)?叫|你改名叫)[「“\"']?([\u4e00-\u9fa5A-Za-z]{1,8})[」”\"']?[吧呀啊。！!，,]*$")


def quick(text: str) -> dict | None:
    """不用模型就能认出来的：「你叫茉莉」「以后叫你小茉」。"""
    m = RENAME.match(text.strip())
    if m:
        from . import settings
        settings.save({"yip": {"soul": {"name": m.group(1)}}})  # 人设卡同步，各处叫法一致
        return add("persona", f"主人给我起名叫{m.group(1)}", source="chat")
    return None


GIVEN_NAME = re.compile(r"(?:起名叫|起名为|名字(?:是|叫)|名叫|改名叫|就叫你|以后叫你)\s*[「“\"']?([\u4e00-\u9fa5A-Za-z]{1,8})")


def remembered_name() -> str:
    """记忆里主人给 Yip 起的名字：最近一条「关于 Yip」里提到的称呼，没有返回空串。"""
    for m in listing("persona"):
        hit = GIVEN_NAME.search(m["content"])
        if hit and hit.group(1) not in ("我", "你", "主人"):
            return hit.group(1)
    return ""


def assistant_name() -> str:
    """Yip 叫什么：人设卡上的名字优先，其次记忆，都没有叫 Yip。"""
    from . import soul
    return soul.name()


EXTRACT = """下面是主人和助理的一来一回。把以后用得上的东西提炼出来；没有就返回空数组。
- persona：主人对助理本身的要求——给它起的名字、怎么称呼、说话口吻（例：「主人给我起名叫茉莉」）
- preference：主人希望以后怎么做事（格式、习惯、不要做什么）
- fact：主人是谁、在哪、做什么
只记主人明确说出来、或者助理已经答应的，不猜；一条一句话。只输出 JSON：{{"memories": [{{"kind": "persona|preference|fact", "content": "…"}}]}}

主人：{text}
助理：{reply}"""


async def learn_from_chat(text: str, reply: str = "") -> list[dict]:
    """聊天里顺带记：主人的话或助理的答复里有明显线索才调快模型，不是每句都问。"""
    if not (HINT.search(text) or AGREED.search(reply or "")) or not llm.configured():
        return []
    try:
        r = await llm.chat_json([{"role": "user", "content": EXTRACT.format(text=text, reply=(reply or "")[:400])}],
                                fast=True, timeout=15)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in (r.get("memories") or [])[:3]:
        row = add(m.get("kind", ""), m.get("content", ""), source="chat")
        if row:
            out.append(row)
    return out


async def import_markdown(text: str) -> list[dict]:
    """把一段 Markdown 个人资料 / 偏好文档导入记忆库：有模型用快模型归纳，没配模型按列表行与段落拆条。"""
    raw = (text or "").strip()
    if not raw:
        return []
    items: list[tuple[str, str]] = []
    if llm.configured():
        try:
            prompt = (
                "把下面这份个人资料或偏好说明拆成多条精简记忆（最多 15 条），只输出 JSON：\n"
                '{"memories": [{"kind": "fact|preference|persona|lesson|state", "content": "一句话"}]}\n\n'
                f"{raw[:5000]}"
            )
            r = await llm.chat_json([{"role": "user", "content": prompt}], fast=True, timeout=25)
            for m in (r.get("memories") or [])[:15]:
                k, c = str(m.get("kind") or "preference"), str(m.get("content") or "").strip()
                if k in KINDS and len(c) >= 3:
                    items.append((k, c))
        except Exception:  # noqa: BLE001
            items = []
    if not items:
        cur_kind = "fact"
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("<!--"):
                continue
            if s.startswith("#"):
                h = s.lstrip("#").strip()
                if re.search(r"偏好|规矩|要求|习惯|风格", h):
                    cur_kind = "preference"
                elif re.search(r"经验|教训|踩坑", h):
                    cur_kind = "lesson"
                elif re.search(r"进展|正在|目标|计划", h):
                    cur_kind = "state"
                else:
                    cur_kind = "fact"
                continue
            s = re.sub(r"^[-*•\d.\s]+", "", s).strip()
            if len(s) >= 4:
                items.append((cur_kind if cur_kind in KINDS else guess_kind(s), s[:200]))
                if len(items) >= 20:
                    break
    out = []
    for k, c in items:
        row = add(k, c, source="manual")
        if row:
            out.append(row)
    return out
