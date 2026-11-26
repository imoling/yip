"""技能：标准 Agent Skills 格式（一个文件夹，SKILL.md 开头写 name 和 description，可带参考资料和脚本）。

技能库两层：内置的在仓库 skills/（只读，随安装包走），主人装的、建的在 data/skills/（同名时用主人的）。
队员能用的 = 包里自带的（导入的队员包才有）+ 从技能库里勾的；没勾过的内置队员按 workers/_skills.json 给默认的。
系统提示里只放「名称：什么时候用」目录，要用时调 load_skill 读全文（渐进披露），做法照 OpenWorker（MIT）。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import db
from ..workers import skill_exec
from . import EXEC, READ, Ctx, Tool, ToolError, clip, obj


@dataclass
class SkillDoc:
    id: str
    name: str
    description: str
    doc: str
    dir: str
    has_script: bool = False
    source: str = "global"      # builtin 内置库 / global 主人装的 / 队员 id（包里自带）


def builtin_dir() -> Path:
    """源码运行在 <仓库>/skills；PyInstaller 冻结后在 sys._MEIPASS/skills。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "skills"
    return db.ROOT / "skills"


def global_dir() -> Path:
    d = db.DATA_DIR / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _join(v: str) -> str:
    """折行的 description 拼回一行：中文之间不留空格。"""
    v = " ".join(v.split())
    return re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", v)


def front(text: str) -> dict:
    """SKILL.md 开头 --- 之间的 YAML；多行的 description（> 或 |）也认。只取顶层的字符串值。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return {}
    try:
        import yaml
        data = yaml.safe_load(m.group(1))
        if isinstance(data, dict):
            return {str(k): (_join(v) if isinstance(v, str) else str(v).lower() if isinstance(v, bool) else v)
                    for k, v in data.items() if isinstance(v, (str, bool, int, float))}
    except Exception:  # noqa: BLE001 — 不合规的 YAML 退回逐行读
        pass
    out = {}
    for ln in m.group(1).splitlines():
        k, sep, v = ln.partition(":")
        if sep and not ln.startswith((" ", "\t")):
            out[k.strip()] = v.strip().strip("\"'")
    return out


def load_dir(d: Path, source: str = "global") -> SkillDoc | None:
    f = d / "SKILL.md"
    if not f.is_file():
        return None
    text = f.read_text("utf-8", errors="replace")
    fm = front(text)
    desc = fm.get("description") or next((ln.strip() for ln in text.split("---")[-1].splitlines()
                                          if ln.strip() and not ln.startswith("#")), "")
    h1 = re.search(r"^#\s+(.+)$", text[text.find("---", 3) + 3:] if text.startswith("---") else text, re.M)
    return SkillDoc(id=d.name, name=(h1.group(1).strip() if h1 else "") or fm.get("name") or d.name,
                    description=desc[:300], doc=text, dir=str(d),
                    has_script=(d / "scripts" / "main.py").is_file(), source=source)


def _scan(root: Path, source: str) -> list[SkillDoc]:
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir() and not d.name.startswith((".", "_")):
            s = load_dir(d, source)
            if s:
                out.append(s)
    return out


def global_skills() -> list[SkillDoc]:
    """主人装的、建的。"""
    return _scan(global_dir(), "global")


def library() -> list[SkillDoc]:
    """整个技能库：内置的 + 主人的，同名时用主人的。"""
    mine = global_skills()
    ids = {s.id for s in mine}
    return [s for s in _scan(builtin_dir(), "builtin") if s.id not in ids] + mine


def defaults(wid: str) -> list[str]:
    """没勾过技能的队员默认用哪些：内置队员看 workers/_skills.json，其他队员一个不给。"""
    from ..workers import registry
    f = registry.BUILTIN_DIR / "_skills.json"
    try:
        return list(json.loads(f.read_text("utf-8")).get(wid) or []) if f.exists() else []
    except json.JSONDecodeError:
        return []


def hidden(doc: str) -> bool:
    """自建队员包里那条「本职」：只为满足包规范，身份已在系统提示里，不算技能。"""
    return front(doc or "").get("hidden") == "true"


def picked(wid: str, allowed: list[str] | None) -> list[str]:
    return defaults(wid) if allowed is None else list(allowed)


def for_worker(card, allowed: list[str] | None = None) -> list[SkillDoc]:
    """队员能用的技能：包里自带的 + 从技能库勾的（allowed 为 None 用默认的）；同名的用技能库里那份。"""
    ids = picked(card.id, allowed)
    lib = {s.id: s for s in library()}
    chosen = [lib[i] for i in ids if i in lib]
    have = {s.id for s in chosen}
    own = [SkillDoc(id=k.id, name=k.name, description=k.description, doc=k.doc, dir=k.dir,
                    has_script=k.has_script, source=card.id) for k in card.skills
           if k.id not in have and not hidden(k.doc)]
    return own + chosen


def catalog(skills: list[SkillDoc]) -> str:
    if not skills:
        return ""
    lines = "\n".join(f"- {s.name}（{s.id}）：{s.description}" for s in skills)
    return ("## 你会的技能\n活和哪个技能对口，先调 load_skill 读全文，照里面的步骤、自查和注意事项做；"
            f"技能里提到的参考文件和脚本在它的技能目录下，用 read_file / run_shell 按需取用。\n{lines}")


def tools_for(card, skills: list[SkillDoc]) -> list[Tool]:
    by = {s.id: s for s in skills} | {s.name: s for s in skills}

    async def load_skill(ctx: Ctx, name: str) -> str:
        s = by.get(name) or by.get((name or "").strip())
        if not s:
            raise ToolError(f"没有叫 {name} 的技能，可用的有：{'、'.join(x.name for x in skills)}")
        files = _files(Path(s.dir))
        tail = f"\n\n（技能目录：{s.dir}" + (f"；里面有：{'、'.join(files)}" if files else "")
        if re.search(rf"~/\.\w+/skills/{re.escape(s.id)}\b", s.doc):
            tail += f"；文中的 ~/.claude/skills/{s.id} 等路径换成这个目录"
        tail += "）"
        return clip(s.doc, 16000) + tail

    out = [Tool("load_skill", "读取一个技能的完整说明（SKILL.md）。",
                obj({"name": {"type": "string", "description": "技能 id 或名称"}}),
                load_skill, READ, lambda a: f"看技能 {a.get('name', '')}", "看技能")] if skills else []
    if card.source == "builtin":
        return out
    for s in skills:
        if not s.has_script or s.source != card.id:
            continue
        skill = next((k for k in card.skills if k.id == s.id), None)
        if not skill:
            continue

        async def run(ctx: Ctx, input: str, _k=skill) -> str:  # noqa: A002
            return await skill_exec.run(card, _k, input, ctx.constraints, 1)

        out.append(Tool(f"skill__{re.sub(r'[^A-Za-z0-9_-]', '_', s.id)[:50]}",
                        f"运行技能「{s.name}」的脚本：{s.description}",
                        obj({"input": {"type": "string", "description": "给这个技能的任务说明"}}),
                        run, EXEC, lambda a, _n=s.name: f"运行技能 {_n}", "运行技能"))
    return out


def _files(d: Path, limit: int = 30) -> list[str]:
    out = []
    for f in sorted(d.rglob("*")):
        if f.is_file() and f.name != "SKILL.md" and not any(x.startswith(".") or x == "__pycache__" for x in f.relative_to(d).parts):
            out.append(str(f.relative_to(d)))
            if len(out) >= limit:
                out.append("…")
                break
    return out


# ---------- 技能库的增删、安装 ----------
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".git", "node_modules", ".venv", "venv")
MAX_BYTES = 50 * 1024 * 1024
GITHUB = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([^/]+)(?:/(.+?))?)?/?$")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")


def create(name: str, description: str, body: str = "", sid: str = "") -> SkillDoc:
    sid = slug(sid) or slug(name) or f"skill-{db.new_id('s')[2:]}"
    d = global_dir() / sid
    if d.exists():
        sid = f"{sid}-{db.new_id('s')[2:6]}"
        d = global_dir() / sid
    d.mkdir(parents=True)
    desc = description.replace("\n", " ").strip()
    (d / "SKILL.md").write_text(
        f"---\nname: {sid}\ndescription: {desc}\n---\n\n# {name}\n\n{(body or description).strip()}\n", "utf-8")
    return load_dir(d)


def read(source: str, sid: str) -> str | None:
    if source in ("global", "builtin"):
        root = global_dir() if source == "global" else builtin_dir()
        f = root / sid / "SKILL.md"
        return f.read_text("utf-8") if f.is_file() and f.parent.parent == root else None
    from ..workers import registry
    card = registry.get(source)
    k = next((k for k in card.skills if k.id == sid), None) if card else None
    return k.doc if k else None


def write(sid: str, doc: str) -> SkillDoc:
    d = global_dir() / sid
    if not d.is_dir() or d.parent != global_dir():
        raise ValueError("内置技能不能直接改，先复制一份")
    fm = front(doc)
    if not fm.get("name") or not fm.get("description"):
        raise ValueError("开头的 --- 里要有 name 和 description")
    (d / "SKILL.md").write_text(doc, "utf-8")
    return load_dir(d)


def copy_builtin(sid: str) -> SkillDoc:
    """把内置技能复制进主人的技能库，之后能改；同名时队员用主人这份。"""
    src = builtin_dir() / sid
    if not (src / "SKILL.md").is_file() or src.parent != builtin_dir():
        raise ValueError("没有这个内置技能")
    dest = global_dir() / sid
    if dest.exists():
        raise ValueError("你的技能库里已经有同名的了")
    shutil.copytree(src, dest, ignore=IGNORE)
    return load_dir(dest)


def remove(sid: str) -> bool:
    d = global_dir() / sid
    if not d.is_dir() or d.parent != global_dir():
        return False
    shutil.rmtree(d)
    return True


def _size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and not f.is_symlink())


def _candidates(root: Path) -> list[Path]:
    """一个技能文件夹，或者装着好几个技能的文件夹（再往下找一层 / skills/ 下一层）。"""
    if (root / "SKILL.md").is_file():
        return [root]
    out = [d for d in sorted(root.iterdir()) if d.is_dir() and (d / "SKILL.md").is_file()]
    if not out and (root / "skills").is_dir():
        out = [d for d in sorted((root / "skills").iterdir()) if d.is_dir() and (d / "SKILL.md").is_file()]
    return out


def _put(dirs: list[Path], replace: bool) -> list[str]:
    names = []
    for d in dirs:
        fm = front((d / "SKILL.md").read_text("utf-8", errors="replace"))
        if not fm.get("description"):
            raise ValueError(f"{d.name} 的 SKILL.md 开头没写 description（什么时候用）")
        if _size(d) > MAX_BYTES:
            raise ValueError(f"{d.name} 超过 50 MB")
        sid = slug(fm.get("name") or d.name) or slug(d.name)
        if not sid:
            raise ValueError(f"{d.name} 的名字要用英文、数字和连字符")
        dest = global_dir() / sid
        if dest.exists():
            if not replace:
                raise ValueError(f"已经装过 {sid}；要换成新的就说「更新」")
            shutil.rmtree(dest)
        # 跳过软链接，免得把技能目录外的东西带进来
        shutil.copytree(d, dest, ignore=lambda p, ns: [n for n in IGNORE(p, ns)] + [n for n in ns if (Path(p) / n).is_symlink()])
        names.append(sid)
    return names


async def install(src: str, replace: bool = False) -> list[str]:
    """装技能：本机文件夹、zip 文件，或 GitHub 链接（仓库、仓库里的某个文件夹）。返回装上的技能 id。"""
    src = (src or "").strip().strip("「」\"'")
    m = GITHUB.match(src)
    if m:
        return await _from_github(*m.groups(), replace=replace)
    p = Path(src).expanduser()
    if p.is_dir():
        dirs = _candidates(p)
        if not dirs:
            raise ValueError("这个文件夹里没有 SKILL.md")
        return _put(dirs, replace)
    if p.is_file() and p.suffix.lower() == ".zip":
        return _from_zip(p, replace)
    raise ValueError("要给本机文件夹、zip 文件或 GitHub 链接")


def _from_zip(zp: Path, replace: bool, sub: str = "") -> list[str]:
    from ..workers import validate
    tmp = Path(tempfile.mkdtemp(dir=db.DATA_DIR))
    try:
        rep, root = validate.safe_extract(zp, tmp / "x")
        if not rep.ok or root is None:
            raise ValueError("；".join(rep.errors) or "压缩包打不开")
        kids = [x for x in root.iterdir() if not x.name.startswith((".", "__MACOSX"))]
        if not (root / "SKILL.md").exists() and len(kids) == 1 and kids[0].is_dir():
            root = kids[0]  # 多包了一层（GitHub 的 仓库-分支/）
        if sub:
            root = root / sub
            if not root.is_dir():
                raise ValueError(f"仓库里没有 {sub} 这个文件夹")
        dirs = _candidates(root)
        if not dirs:
            raise ValueError("里面没有 SKILL.md")
        return _put(dirs, replace)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _from_github(owner: str, repo: str, ref: str | None, sub: str | None, replace: bool) -> list[str]:
    import httpx
    url = f"https://github.com/{owner}/{repo}/archive/{ref or 'HEAD'}.zip"
    tmp = Path(tempfile.mkdtemp(dir=db.DATA_DIR))
    try:
        zp = tmp / "repo.zip"
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
            r = await c.get(url)
        if r.status_code != 200:
            raise ValueError(f"下载失败：GitHub 返回 {r.status_code}，看看链接对不对、仓库是不是公开的")
        if len(r.content) > MAX_BYTES:
            raise ValueError("仓库太大了，给到具体技能文件夹的链接")
        zp.write_bytes(r.content)
        return _from_zip(zp, replace, sub or "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
