"""新建队员：一句话让模型起草表单；表单落成队员包，校验通过才上岗。"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from .. import db, llm
from . import registry, validate

AGENT_MD = """---
name: {id}
version: "1.0.0"
description: "{name}：{introduction}"
---

# {name}

## 身份

{identity}

## 工作方式

1. 收到任务后，先看「你会的技能」里有没有对口的，有就用 load_skill 读它的做法再动手。
2. 每轮开始前重读约束；约束和原任务冲突时以约束为准。
3. 只写查到的事实，不编数字；查不到、打不开就如实说明。
4. 没有对口的技能时，用通用的搜索、读网页、读写文件把事做完，并说明哪些超出了专长。

## 能力路由

{routes}

## 需要的输入

{inputs}

## 交付标准

{deliverable}

## 安全规则

- 只在工作文件夹里读写，不删别人的文件，不发消息、不对外提交任何东西。

## 信息保护

- 不在产出里写个人联系方式、密钥和内部账号。
"""

SKILL_MD = """---
name: {id}
version: 1.0.0
description: |
  {description}
  不适用：{not_for}。
---

# {name}

## 输入

任务说明和当前约束。

## 步骤

1. 按任务说明完成「{name}」，结果 5 行以内，不知道的写【xx】，不编数字。
"""

CORE_MD = """---
name: core
version: 1.0.0
hidden: true
description: |
  {name}的本职，身份和交付标准见系统提示。
  不适用：技能库里有对口技能的活，先用那个技能。
---

# {name}的本职

按系统提示里的身份、需要的输入和交付标准把活干完；真正的做法在技能库里勾给它的技能里。
"""

ID_RE = re.compile(r"[^a-z0-9-]+")


def _slug(text: str, prefix: str) -> str:
    s = ID_RE.sub("-", (text or "").lower()).strip("-")
    return s[:40] if s and s[0].isalnum() else f"{prefix}-{uuid.uuid4().hex[:6]}"


def normalize(form: dict) -> dict:
    f = {k: (v.strip() if isinstance(v, str) else v) for k, v in form.items()}
    f["name"] = f.get("name") or "未命名队员"
    f["id"] = _slug(f.get("id", ""), "worker")
    pers = [p.strip() for p in f.get("personality") or [] if p and p.strip()][:5]
    while len(pers) < 3:
        pers.append(["按约束办事", "不编数字", "交付可核对"][len(pers)])
    f["personality"] = pers
    skills = []
    for i, s in enumerate(f.get("skills") or []):
        if not s.get("name"):
            continue
        skills.append({
            "name": s["name"].strip(), "id": _slug(s.get("id", ""), f"skill-{i + 1}"),
            "description": (s.get("description") or s["name"]).strip(),
            "not_for": (s.get("not_for") or "超出本队员能力的事").strip(),
        })
    if not skills:  # 技能都从技能库勾：包里只放一条隐藏的「本职」，满足包规范
        skills.append({"name": f"{f['name']}的本职", "id": "core", "description": f.get("introduction") or f["name"],
                       "not_for": "超出本队员能力的事", "core": True})
    f["skills"] = skills
    f["capability"] = f.get("capability") or f["name"]
    f["introduction"] = (f.get("introduction") or "")[:200]
    return f


def build(form: dict, dest: Path) -> Path:
    f = normalize(form)
    root = dest
    adir = root / f["id"]
    (adir / "agents").mkdir(parents=True)
    rows = "\n".join(f"| {s['description']} | `{s['id']}` {s['name']} |" for s in f["skills"])
    for s in f["skills"]:
        sd = adir / "skills" / s["id"]
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(CORE_MD.format(name=f["name"]) if s.get("core") else SKILL_MD.format(**s), "utf-8")
    meta = {
        "name": f["name"], "type": "cc", "version": "1.0.0", "introduction": f["introduction"],
        "personality": f["personality"],
        "capabilities": [{"name": f["capability"], "description": f.get("capability_desc") or f["introduction"][:60] or f["capability"],
                          "skills": [{"name": s["name"], "id": s["id"], "description": s["description"]} for s in f["skills"]]}],
    }
    if f.get("sample"):
        meta["prompts"] = [{"title": "试一下", "subtitle": "建好先跑一遍", "content": f["sample"]}]
    (root / "yip.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    (adir / "agents" / f"{f['id']}.md").write_text(AGENT_MD.format(
        id=f["id"], name=f["name"], introduction=f["introduction"],
        identity=f.get("identity") or f"你是{f['name']}。{f['introduction']}",
        routes=f"### {f['capability']}\n\n| 用户说 | 技能 |\n|---|---|\n{rows}",
        inputs=f.get("inputs") or "要做的事和期望的交付形式",
        deliverable=f.get("deliverable") or "结果清单，每条可核对"), "utf-8")
    return root


def staging() -> Path:
    d = db.DATA_DIR / "staging" / uuid.uuid4().hex[:10]
    d.parent.mkdir(parents=True, exist_ok=True)
    return d


def _prefs_from(form: dict, wid: str) -> None:
    kw = {}
    if form.get("avatar"):
        kw["avatar"] = str(form["avatar"])
    if "global_skills" in form:
        kw["skills"] = form["global_skills"]
    if kw:
        registry.set_prefs(wid, **kw)


def save(form: dict, source: str = "custom"):
    st = staging()
    try:
        root = build(form, st)
        rep, card = registry.install(root, source, enabled=True)
        if card:
            _prefs_from(form, card.id)
        return rep, card
    finally:
        shutil.rmtree(st, ignore_errors=True)


def sample(card) -> str:
    """「试一下」的预填任务：包里 prompts 的第一条，没有就按第一个技能拼一句。"""
    import json as _json
    try:
        mj = validate.manifest_path(Path(card.root))
        meta = _json.loads(mj.read_text("utf-8")) if mj else {}
        for p in meta.get("prompts") or []:
            if p.get("content"):
                return p["content"]
    except Exception:  # noqa: BLE001
        pass
    real = [k for k in card.skills if "hidden: true" not in k.doc]
    if real:
        return f"{real[0].description}，先做一个小样给我看"
    return f"用你的本事帮我做一件小事：{card.introduction[:40]}"


def _not_for(doc: str) -> str:
    for ln in (doc or "").splitlines():
        if "不适用" in ln:
            return ln.split("：", 1)[-1].split(":", 1)[-1].strip().rstrip("。")
    return ""


def to_form(card) -> dict:
    """已有队员 → 表单，给编辑和「复制一份改」用。技能库里勾的放 global_skills；skills 只剩老的自带技能。"""
    from ..tools import skills as t_skills
    from .package import section
    return {
        "global_skills": t_skills.picked(card.id, registry.prefs(card.id)["skills"]),
        "id": card.id, "name": card.name, "introduction": card.introduction, "personality": list(card.personality),
        "capability": (card.capabilities[0].get("name") if card.capabilities else card.name) or card.name,
        "skills": [{"id": k.id, "name": k.name, "description": k.description, "not_for": _not_for(k.doc)} for k in card.skills
                   if not t_skills.hidden(k.doc)],
        "inputs": card.inputs, "deliverable": card.deliverable, "identity": section(card.prompt, "身份"),
        "sample": sample(card),
    }


def update(wid: str, form: dict):
    """只改自建队员：按表单重新生成包，校验通过后整个替换。上岗状态、头像等偏好不动。"""
    card = registry.get(wid)
    if not card or card.source != "custom":
        rep = validate.Report()
        rep.errors.append("只能改自建的队员；内置和导入的可以复制一份再改")
        return rep, None
    st = staging()
    try:
        root = build({**form, "id": wid}, st)
        rep = validate.validate_dir(root)
        if not rep.ok:
            return rep, None
        dest = registry.user_dir() / wid
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(root, dest)
        registry.reload()
        _prefs_from(form, wid)
        return rep, registry.get(wid)
    finally:
        shutil.rmtree(st, ignore_errors=True)


def copy(wid: str):
    card = registry.get(wid)
    if not card:
        return None, None
    f = to_form(card)
    f["id"] = ""
    f["name"] = f"{card.name}（副本）"[:12]
    return save(f)


def import_zip(zip_path: Path):
    """解压到新的空目录 → 校验 → 装进 data/workers/（默认不上岗，等人点「上岗」）。"""
    st = staging()
    try:
        rep, root = validate.safe_extract(zip_path, st)
        if not rep.ok or root is None:
            return rep, None
        rep2, card = registry.install(root, "imported", enabled=False)
        return rep2, card
    finally:
        shutil.rmtree(st, ignore_errors=True)


DRAFT_PROMPT = """用户想新建一个队员，请起草它的能力卡片。只输出 JSON，字段：
{{"name": "中文名，≤8 字，以「员」「专员」「助理」结尾",
 "id": "英文 kebab-case 标识，如 contract-reviewer",
 "introduction": "一两句话说它干什么、不干什么，≤80 字",
 "personality": ["3 到 5 个短语，每个 ≤6 字"],
 "capability": "能力分组名，≤6 字",
 "global_skills": ["从下面的技能库里挑对口的技能 id，0 到 4 个，不对口就别挑"],
 "missing": ["技能库里没有、但这个队员很需要的技能，一句话描述，0 到 2 条"],
 "inputs": "它干活需要用户给什么",
 "deliverable": "交付标准，一句话，可验收",
 "identity": "第二人称的身份说明，2–3 句，写清服务谁、边界在哪",
 "sample": "建好后拿来试的一句任务，具体、5 分钟内能做完"}}
技能库：
{library}
已有队员（别重复）：{existing}
用户的话：{text}"""


SKILL_PROMPT = """用户想新建一个技能：给 AI 队员看的操作手册，标准 Agent Skills 格式（SKILL.md）。
队员能用的工具：web_search、web_fetch、browser_open / browser_click / browser_screenshot（要点按钮、要登录的网页）、
read_file（也能读 Word / Excel / PPT / PDF）、write_file、edit_file、run_shell、make_pptx、make_docx、make_xlsx、make_pdf、
check_page（打开网页跑脚本自查）。

写得像一位做过很多次的老手给新人的交代：具体、能照做、能核对，不写空话。只输出 JSON：
{{"id": "英文 kebab-case，如 bid-checklist",
 "name": "技能名，中文 ≤10 字",
 "description": "什么时候用它，一句话 ≤80 字，后面加「；不适用：…」写清不该用它的场景（队员靠这句判断要不要用）",
 "body": "Markdown 正文，按这几节写：\n## 怎么做（编号步骤，每步写清用哪个工具、判断标准是什么；有固定格式就给模板）\n## 交付（什么格式、放在这件事的文件夹里、文件名怎么起、最后回复写什么）\n## 交付前自查（3 到 6 条能逐条核对的检查项）\n## 注意（常见的坑、口径、不做什么）"}}
用户的话：{text}"""


async def draft_skill(text: str) -> dict:
    r = await llm.chat_json([{"role": "user", "content": SKILL_PROMPT.format(text=text)}], timeout=90)
    return {"id": _slug(str(r.get("id", "")), "skill"), "name": str(r.get("name", ""))[:20],
            "description": str(r.get("description", ""))[:200], "body": str(r.get("body", ""))}


async def draft(text: str) -> dict:
    from ..tools import skills as t_skills
    lib = t_skills.library()
    existing = "、".join(c.name for c in registry.all_cards())
    catalog = "\n".join(f"- {s.id}：{s.name}，{s.description}" for s in lib) or "（空）"
    r = await llm.chat_json([{"role": "user", "content": DRAFT_PROMPT.format(text=text, existing=existing, library=catalog)}],
                            timeout=60, fast=True)
    ids = {s.id for s in lib}
    f = normalize({**r, "skills": []})
    f["skills"] = []
    f["global_skills"] = [i for i in r.get("global_skills") or [] if i in ids][:4]
    f["missing"] = [str(x) for x in r.get("missing") or []][:2]
    f["identity"] = r.get("identity", "")
    f["sample"] = r.get("sample", "")
    f["capability"] = r.get("capability") or f["capability"]
    return f
