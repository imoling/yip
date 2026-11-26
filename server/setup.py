"""Yip 在聊天里查、改自己的设置：用什么搜索、什么模式、哪个引擎、接哪些连接器；主人贴 key 过来，她自己填进去、试连一次。
连接器在对话里配和在「连接器」页手动配，改的是同一份 data/mcp.json。

key 不落地：主人发来的话里一出现像 key 的东西，进库、进模型、进记忆之前就换成「【密钥 tvly-…a3F9】」，
真值只在内存里留 30 分钟，工具拿到这个占位符再换回真值。所以聊天记录、模型、记忆里都没有完整的 key。
"""
from __future__ import annotations

import re
import time

from . import settings
from .tools import mail as t_mail
from .tools import mcp as t_mcp
from .tools import mcp_presets
from .tools import web as t_web

KEYISH = re.compile(r"\b(?:tvly-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,})")
LONG = re.compile(r"(?<![\w/.-])[A-Za-z0-9_-]{28,}(?![\w/.-])")
HINT = re.compile(r"(?i)key|密钥|令牌|token|api")
_VAULT: dict[str, tuple[str, float]] = {}
TTL = 30 * 60


def hide_secrets(text: str) -> tuple[str, int]:
    """把话里像 key 的东西换成占位符；返回（换过的话，换了几个）。说了 key / 密钥 才认长串，免得误伤链接和编号。"""
    now = time.time()
    for k in [k for k, (_, t) in _VAULT.items() if now - t > TTL]:
        _VAULT.pop(k, None)
    found = KEYISH.findall(text)
    if HINT.search(text):
        found += [m for m in LONG.findall(text) if re.search(r"\d", m) and re.search(r"[A-Za-z]", m)]
    n = 0
    for sec in dict.fromkeys(found):
        head = sec.split("-")[0] + "-" if "-" in sec[:6] else ""
        ph = f"【密钥 {head}…{sec[-4:]}】"
        _VAULT[ph] = (sec, now)
        text = text.replace(sec, ph)
        n += 1
    return text, n


def reveal(v: str) -> str:
    """工具拿到的是占位符就换回真值；不是占位符原样返回（主人在设置页填的那种）。"""
    v = (v or "").strip()
    hit = _VAULT.get(v)
    if hit:
        return hit[0]
    m = re.search(r"【密钥 [^】]*】", v)
    return _VAULT.get(m.group(0), ("", 0))[0] if m else v


# ---------- 给聊天用的工具（OpenAI function calling 形状） ----------
def _fn(name: str, desc: str, props: dict | None = None, req: list | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": req or []}}}


MODES = {"auto": "自动", "plan": "计划", "ask_writes": "手动", "allow_all": "全部放行", "ask_all": "每步都问"}
TOOLS = [
    _fn("get_setup", "查自己现在的设置：搜索服务、模式、干活的方式、模型、邮箱、连接器、工作文件夹"),
    _fn("list_connectors", "看能接哪些连接器（预置的）、哪些已经接上、要填什么"),
    _fn("add_connector", "接一个预置连接器（比如 tavily 深度搜索、github、amap）。会先试连，通了才保存；已经接过的就更新",
        {"preset": {"type": "string", "description": "预置的 id，先用 list_connectors 查"},
         "values": {"type": "object", "description": "要填的空，键是字段名（比如 key）；主人给的 key 在对话里显示成【密钥 …】，原样填"}},
        ["preset"]),
    _fn("set_connector", "停用或启用一个已接的连接器", {"name": {"type": "string"}, "enabled": {"type": "boolean"}},
        ["name", "enabled"]),
    _fn("remove_connector", "删掉一个已接的连接器", {"name": {"type": "string"}}, ["name"]),
    _fn("set_search", "换平时用的免费 / 付费搜索服务（深度搜索是 Tavily 连接器，用 add_connector 接）",
        {"provider": {"type": "string", "enum": ["keenable", "ddgs", "tavily", "bocha"], "description": "keenable、ddgs 免 key；tavily、bocha 要 key"},
         "api_key": {"type": "string", "description": "主人给的 key，原样填【密钥 …】"}}, ["provider"]),
    _fn("search_history", "翻聊天记录（包括清屏前的）：主人问起之前说过的话、交过的文件、发过的链接，前情里没有细节时先翻一下再答",
        {"q": {"type": "string", "description": "关键词，比如「定价」「阿森纳」"},
         "kind": {"type": "string", "enum": ["", "file", "image", "link"]},
         "date": {"type": "string", "description": "某一天，YYYY-MM-DD，可不填"}}, []),
    _fn("read_url", "读取一个网页或微信公众号文章链接的正文内容（会自动用本机浏览器打开）。主人发了链接问里面写了什么、能不能做到时，先用它读完原文再回答",
        {"url": {"type": "string", "description": "完整网址，以 http:// 或 https:// 开头"}}, ["url"]),
    _fn("set_persona", "改自己的人设卡：名字、怎么称呼主人、性格、和主人的关系、表情多少、对外自我介绍、白天要不要主动找主人。只传要改的",
        {"name": {"type": "string", "description": "主人给你起的名字，≤8 字"},
         "call_me": {"type": "string", "description": "怎么称呼主人，比如「老板」「王姐」「小李」；不称呼就传空串"},
         "tone": {"type": "string", "enum": ["steady", "warm", "cheeky"], "description": "steady 干练带点温度 / warm 温和 / cheeky 嘴欠心软"},
         "relation": {"type": "string", "enum": ["assistant", "partner", "friend"], "description": "助理 / 搭档 / 朋友"},
         "emoji": {"type": "string", "enum": ["none", "few"]},
         "intro": {"type": "string", "description": "别人问你是谁时的固定说法，主人定的原话"},
         "proactive": {"type": "boolean", "description": "工作日白天偶尔主动说两句"}}, []),
    _fn("add_style_rule", "主人纠正你说话或做事的方式时，记成一条以后都照着做的规矩（比如「回答别超过两句」「别叫我老板」）",
        {"rule": {"type": "string", "description": "一句话，写成陈述句：「回答控制在两句以内」"}}, ["rule"]),
    _fn("list_skills", "看技能库里有哪些技能、每个队员会哪些"),
    _fn("install_skill", "装技能进技能库：本机文件夹（比如 ~/.claude/skills/humanizer-zh，或装着好几个技能的文件夹）、zip 文件、GitHub 链接",
        {"source": {"type": "string", "description": "文件夹路径、zip 路径或 GitHub 链接"},
         "replace": {"type": "boolean", "description": "已经装过的换成新的（主人说「更新」时）"},
         "give": {"type": "array", "items": {"type": "string"}, "description": "装好后顺手给哪些队员用（队员 id 或名字），可不填"}},
        ["source"]),
    _fn("give_skill", "给队员加上或去掉技能库里的一个技能",
        {"member": {"type": "string", "description": "队员 id 或名字"}, "skill": {"type": "string", "description": "技能 id"},
         "on": {"type": "boolean", "description": "true 加上，false 去掉"}}, ["member", "skill", "on"]),
    _fn("set_mode", "换动手前要不要问的模式", {"mode": {"type": "string", "enum": ["auto", "plan", "ask_writes", "allow_all"]}},
        ["mode"]),
    _fn("set_engine", "换队员干活的方式：classic 标准；deep 用 Deep Agents（试验，会先列计划，长任务稳一些但慢一些）",
        {"engine": {"type": "string", "enum": ["classic", "deep"]}}, ["engine"]),
]


def get_setup() -> str:
    y = settings.yip()
    s = y["search"]
    d = settings.load()
    daily = t_web.PROVIDERS.get(s.get("provider") or "keenable", s.get("provider"))
    dp, dk = t_web.deep_search()
    deep = t_web.PROVIDERS.get(dp, "")
    conns = [n for n, c in t_mcp.load().items() if c.get("enabled", True)]
    return "\n".join([
        f"平时搜索：{daily}" + ("（免 key）" if (s.get('provider') or 'keenable') in ('ddgs', 'keenable') else ("（key 已填）" if s.get("api_key") else "（还没填 key）"))
        + "；搜不动会自动换另一家免费的",
        f"深度搜索：{deep + '（连接器已接）' if deep and dk else '没配（接上 Tavily 连接器就有）'}；平时的搜失败了也会换它",
        f"读网页：先直接取正文，取不到换 Keenable 抓取",
        f"模式：{MODES.get(y['approval'], y['approval'])}",
        f"干活的方式：{'Deep Agents（试验）' if y.get('engine') == 'deep' else '标准'}",
        f"日常模型：{d['fast_model'] or '没配（都用难活模型）'}；难活模型：{d['model'] or '没配'}"
        "（队员先用日常模型，难的活、返工、卡住时换难活模型；验收用难活模型）",
        f"邮箱：{'已接' if t_mail.configured() else '没接'}",
        f"连接器：{'、'.join(conns) or '没有'}",
        f"工作文件夹：{settings.workspace()}",
    ])


async def set_search(provider: str, api_key: str = "") -> str:
    """平时用的搜索服务。"""
    if provider not in t_web.PROVIDERS:
        return f"不认识 {provider}，能用的有：{'、'.join(t_web.PROVIDERS)}"
    key = reveal(api_key)
    if provider not in ("ddgs", "keenable") and not key:
        old = settings.yip()["search"]
        if not (old.get("api_key") and old.get("provider") == provider):
            return f"{t_web.PROVIDERS[provider]} 要 key，请主人发过来"
        key = old["api_key"]
    try:
        rows = await t_web.search_with(provider, key, "OpenAI", 3)
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "response", None), "status_code", "") or type(e).__name__
        return (f"试搜没通（{code}），没有保存：key 不对、过期或者没额度。"
                "注意【密钥 …】是打过码的样子，完整的 key 已经收到并拿去试过了，不是没发全")
    if provider not in ("ddgs", "keenable") and not rows:
        return "试搜没有结果，没有保存；key 可能不对"
    settings.save({"yip": {"search": {"provider": provider, "api_key": key}}})
    return f"已保存，平时搜索用 {t_web.PROVIDERS[provider]}，试搜了一次，通了（{len(rows)} 条结果）"


def list_connectors() -> str:
    have = t_mcp.load()
    by_preset = {c.get("preset"): n for n, c in have.items() if c.get("preset")}
    lines = ["已接的："]
    for n, c in have.items():
        lines.append(f"- {n}：{'停用了' if c.get('enabled') is False else '在用'}")
    if len(lines) == 1:
        lines.append("- （还没有）")
    lines.append("能接的预置：")
    for p in mcp_presets.public():
        fields = "、".join(f["label"] for f in p["fields"]) or "不用填"
        state = f"已接（{by_preset[p['id']]}）" if p["id"] in by_preset else ("能接" if p["ready"] else f"要先装 {p['needs']}")
        lines.append(f"- {p['id']}「{p['name']}」：{p['desc']}；要填：{fields}；{state}")
    return "\n".join(lines)


async def add_connector(preset: str, values: dict | None = None) -> str:
    p = mcp_presets.BY_ID.get(preset)
    if not p:
        return f"没有 {preset} 这个预置，先用 list_connectors 看看"
    vals = {k: reveal(str(v)) for k, v in (values or {}).items()}
    if len(p["fields"]) == 1 and not vals.get(p["fields"][0]["key"]) and vals:  # 模型把字段名写错了：只有一个空就填它
        vals = {p["fields"][0]["key"]: next(iter(vals.values()))}
    try:
        cfg = mcp_presets.build(preset, vals)
    except ValueError as e:
        return f"{e}，请主人发过来"
    have = t_mcp.load()
    name = next((n for n, c in have.items() if c.get("preset") == preset), p["name"])
    await t_mcp.disconnect(name)
    r = await t_mcp.test(name, cfg)
    if not r.get("ok"):
        await t_mcp.disconnect(name)
        return (f"试连没通（{r.get('error')}），没有保存。"
                "【密钥 …】是打过码的样子，完整的 key 已经拿去试过了")
    have[name] = cfg
    t_mcp.save(have)
    return f"「{name}」接好了，试连通了，有 {len(r.get('tools') or [])} 个工具；队员下一件事起就能用"


async def set_connector(name: str, enabled: bool) -> str:
    have = t_mcp.load()
    if name not in have:
        return f"没有叫 {name} 的连接器，已接的有：{'、'.join(have) or '（没有）'}"
    have[name]["enabled"] = bool(enabled)
    t_mcp.save(have)
    await t_mcp.disconnect(name)
    return f"「{name}」{'启用' if enabled else '停用'}了"


async def remove_connector(name: str) -> str:
    have = t_mcp.load()
    if name not in have:
        return f"没有叫 {name} 的连接器"
    have.pop(name)
    t_mcp.save(have)
    await t_mcp.disconnect(name)
    return f"「{name}」删掉了"


def set_mode(mode: str) -> str:
    if mode not in MODES:
        return "没有这个模式"
    settings.save({"yip": {"approval": mode}})
    return f"已切到「{MODES[mode]}」"


def set_engine(engine: str) -> str:
    settings.save({"yip": {"engine": engine}})
    return f"已切到「{'Deep Agents（试验）' if engine == 'deep' else '标准'}」，下一件事开始生效"


def _member(who: str):
    from .workers import registry
    who = (who or "").strip()
    return registry.get(who) or next((c for c in registry.all_cards() if c.name == who or who in c.name), None)


def list_skills() -> str:
    from .tools import skills as t_skills
    from .workers import registry
    lib = t_skills.library()
    if not lib:
        return "技能库是空的"
    out = ["技能库：" + "；".join(f"{s.id}（{s.name}{'，内置' if s.source == 'builtin' else ''}）：{s.description[:60]}" for s in lib)]
    for c in registry.active():
        mine = t_skills.for_worker(c, registry.prefs(c.id)["skills"])
        out.append(f"- {c.name}（{c.id}）会：{'、'.join(k.id for k in mine) or '（没有）'}")
    return "\n".join(out)


def give_skill(member: str, skill: str, on: bool) -> str:
    from .tools import skills as t_skills
    from .workers import registry
    c = _member(member)
    if not c:
        return f"没有叫 {member} 的队员"
    if on and skill not in {s.id for s in t_skills.library()}:
        return f"技能库里没有 {skill}，先用 install_skill 装上"
    cur = t_skills.picked(c.id, registry.prefs(c.id)["skills"])
    registry.set_prefs(c.id, skills=[x for x in cur if x != skill] + ([skill] if on else []))
    return f"{c.name}{'会用' if on else '不再用'} {skill} 了，下一件事开始生效"


async def install_skill(source: str, replace: bool = False, give: list | None = None) -> str:
    from .tools import skills as t_skills
    try:
        names = await t_skills.install(source, replace)
    except ValueError as e:
        return f"没装上：{e}"
    except Exception as e:  # noqa: BLE001
        return f"没装上：{type(e).__name__}"
    lines = [f"装好了：{'、'.join(names)}"]
    for who in give or []:
        for n in names:
            lines.append(give_skill(str(who), n, True))
    if not give:
        lines.append("还没给队员用，问主人给谁，或者按技能内容挑对口的队员用 give_skill 加上")
    return "\n".join(lines)


def set_persona(args: dict) -> str:
    from . import soul
    patch = soul.clean_card({k: v for k, v in args.items() if v is not None})
    if not patch:
        return "没有要改的"
    settings.save({"yip": {"soul": {**patch, "asked": True}}})
    names = {"name": "名字", "call_me": "称呼", "tone": "性格", "relation": "关系", "emoji": "表情", "intro": "自我介绍",
             "proactive": "主动找你"}
    shown = {"tone": lambda v: soul.TONES[v][0], "relation": lambda v: soul.RELATIONS[v][0], "emoji": lambda v: soul.EMOJI[v][0],
             "proactive": lambda v: "开" if v else "关"}
    return "改好了：" + "；".join(f"{names[k]} {shown.get(k, str)(v) if v != '' else '不用'}" for k, v in patch.items()) + "，下一句开始生效"


def add_style_rule(rule: str) -> str:
    from . import memory
    rule = (rule or "").strip()
    if len(rule) < 2:
        return "规矩是空的"
    memory.add("persona", rule, source="feedback")
    return f"记下了：{rule}"


async def call(name: str, args: dict) -> str:
    if name == "set_persona":
        return set_persona(args)
    if name == "add_style_rule":
        return add_style_rule(str(args.get("rule") or ""))
    if name == "list_skills":
        return list_skills()
    if name == "install_skill":
        return await install_skill(str(args.get("source") or ""), bool(args.get("replace")),
                                   args.get("give") if isinstance(args.get("give"), list) else None)
    if name == "give_skill":
        return give_skill(str(args.get("member") or ""), str(args.get("skill") or ""), args.get("on") is not False)
    if name == "get_setup":
        return get_setup()
    if name == "set_search":
        return await set_search(str(args.get("provider") or ""), str(args.get("api_key") or ""))
    if name == "search_history":
        from . import chatlog
        rows = chatlog.search(str(args.get("q") or ""), str(args.get("kind") or ""), str(args.get("date") or ""), limit=12)
        if not rows:
            return "聊天记录里没找到"
        import datetime as dt
        return "\n".join(
            f"- {dt.datetime.fromtimestamp(r['created_at'] / 1000).strftime('%m-%d %H:%M')} {'主人' if r['role'] == 'user' else '我'}："
            f"{r['content'][:200]}" + "".join(f"［文件 {d.get('name')}：{d.get('path', '')}］" for d in r["docs"][:3])
            for r in rows)
    if name == "read_url":
        from .tools import Ctx, ToolError
        url = str(args.get("url") or "").strip()
        try:
            return await t_web.web_fetch(Ctx(task_id="chat", main_id="chat", worker_id="yip", workspace=settings.workspace()), url)
        except ToolError as e:
            return f"没读出来：{e}"
        except Exception as e:  # noqa: BLE001
            return f"没读出来：{type(e).__name__}"
    if name == "list_connectors":
        return list_connectors()
    if name == "add_connector":
        return await add_connector(str(args.get("preset") or ""), args.get("values") if isinstance(args.get("values"), dict) else {})
    if name == "set_connector":
        return await set_connector(str(args.get("name") or ""), bool(args.get("enabled")))
    if name == "remove_connector":
        return await remove_connector(str(args.get("name") or ""))
    if name == "set_mode":
        return set_mode(str(args.get("mode") or ""))
    if name == "set_engine":
        return set_engine(str(args.get("engine") or ""))
    return f"没有 {name} 这个工具"
