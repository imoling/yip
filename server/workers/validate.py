"""队员包校验：对目录和 zip 包做「拒绝 / 提示」两级静态检查。"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ALLOWED_EXT = set(""".yaml .yml .json .md .txt .py .js .ts .tsx .css .html
.pdf .docx .csv .xlsx .pptx .png .jpg .svg
.sh .bash .toml .ini .cfg .sql .r .ipynb .j2 .jar .mjs .cjs""".split())
TOP_FIELDS = {"name", "type", "version", "introduction", "personality", "capabilities", "prompts"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SENSITIVE = [
    (re.compile(r"AKIA[A-Z0-9]{16}"), "AWS 密钥"),
    (re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY"), "私钥块"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI 风格 Key"),
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}"), "token/password 赋值"),
]
REQUIRED_SECTIONS = ("身份", "能力路由", "安全规则", "信息保护")
ZIP_MAX = 50 * 1024 * 1024
UNZIP_MAX = 200 * 1024 * 1024
FILES_MAX = 1000
SKIP_DIRS = {".git", ".svn", ".hg", "__pycache__"}
MANIFEST_NAMES = ("yip.json", "kooky.json")


def manifest_path(root: str | Path) -> Path | None:
    """包根清单：优先 yip.json，兼容历史包里的 kooky.json。"""
    p = Path(root)
    for name in MANIFEST_NAMES:
        f = p / name
        if f.exists():
            return f
    return None


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warns": self.warns}


def frontmatter(text: str) -> tuple[dict | None, str]:
    """解析 YAML frontmatter 的平铺字段；支持 `key: |` 块标量（续行合并成一行）。"""
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    fm, key = {}, None
    for line in parts[1].splitlines():
        if line.startswith((" ", "\t")) and key:
            fm[key] = (fm[key] + " " + line.strip()).strip()
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip().strip('"').strip("'")
            fm[key] = "" if v in ("|", ">", "|-", ">-") else v
    return fm, parts[2]


def validate_dir(root: str | Path) -> Report:
    rep = Report()
    err, warn = rep.errors.append, rep.warns.append
    root = Path(root).resolve()

    files, total = [], 0
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        for f in fs:
            p = Path(d) / f
            rel = p.relative_to(root).as_posix()
            if f == ".DS_Store":
                continue
            ext = p.suffix.lower()
            if f == ".env" or ext == ".env":
                err(f".env 不得入包：{rel}")
                continue
            if f.startswith(".") and f not in {".agenthubignore", ".gitignore"}:
                warn(f"隐藏文件会被跳过：{rel}")
                continue
            if ext in {".zip", ".tar", ".gz", ".tgz"}:
                warn(f"嵌套压缩包会被跳过：{rel}")
                continue
            if ext == ".pyc":
                continue
            if ext not in ALLOWED_EXT and f not in {".agenthubignore", ".gitignore"}:
                warn(f"扩展名不在白名单，会被跳过：{rel}")
            files.append(p)
            total += p.stat().st_size
            if ext in {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".toml", ".ini", ".cfg", ".js", ".ts"}:
                try:
                    txt = p.read_text("utf-8")
                except UnicodeDecodeError:
                    err(f"不是 UTF-8：{rel}")
                    continue
                for rx, label in SENSITIVE:
                    if rx.search(txt):
                        warn(f"疑似敏感信息（{label}）：{rel}")
    if len(files) > FILES_MAX:
        err(f"文件数 {len(files)} 超过 {FILES_MAX}")
    if total > UNZIP_MAX:
        err(f"总大小 {total / 1e6:.1f} MB 超过 200 MB")

    mj = manifest_path(root)
    if mj is None:
        err("包根缺少 yip.json")
        return rep
    mname = mj.name
    try:
        meta = json.loads(mj.read_text("utf-8"))
    except Exception as e:
        err(f"{mname} 不是合法 JSON：{e}")
        return rep
    extra = set(meta) - TOP_FIELDS
    if extra:
        err(f"{mname} 出现规范外顶层字段：{sorted(extra)}")
    for k in ("name", "type", "version"):
        if not isinstance(meta.get(k), str) or not meta.get(k):
            err(f"{mname}.{k} 必填且为非空字符串")
    if meta.get("type") not in ("cc", "kc"):
        err(f"{mname}.type 只能是 cc 或 kc")
    if isinstance(meta.get("version"), str) and not SEMVER_RE.match(meta["version"]):
        err(f"{mname}.version 应为三段式：{meta['version']}")
    intro = meta.get("introduction")
    if intro is not None and (not isinstance(intro, str) or not intro or len(intro) > 200):
        err("introduction 应为非空且不超过 200 字")
    pers = meta.get("personality")
    if pers is not None and not (isinstance(pers, list) and 3 <= len(pers) <= 5 and all(isinstance(x, str) and x for x in pers)):
        err("personality 应为 3–5 个非空字符串")
    skill_ids = set()
    caps = meta.get("capabilities")
    if caps is not None:
        if not (isinstance(caps, list) and caps):
            err("capabilities 至少 1 组")
        for c in caps or []:
            for k in ("name", "description", "skills"):
                if k not in c:
                    err(f"能力分组缺少 {k}：{c.get('name')}")
            if not c.get("skills"):
                err(f"能力分组 {c.get('name')} 的 skills 至少 1 条")
            for s in c.get("skills") or []:
                for k in ("name", "id", "description"):
                    if not s.get(k):
                        err(f"skill 条目缺少 {k}：{s}")
                if s.get("id") and not NAME_RE.match(s["id"]):
                    err(f"skill id 不是 kebab-case：{s['id']}")
                skill_ids.add(s.get("id"))
    for p in meta.get("prompts") or []:
        if len(p.get("title", "")) > 12:
            warn(f"prompt 标题超 12 字：{p.get('title')}")
        if len(p.get("subtitle", "")) > 64:
            err(f"prompt subtitle 超 64 字：{p.get('title')}")
        if len(p.get("content", "")) > 1024:
            err(f"prompt content 超 1024 字：{p.get('title')}")
        for k in ("title", "subtitle", "content"):
            if not p.get(k):
                err(f"prompt 缺少 {k}：{p}")

    agent_dirs = [d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name not in SKIP_DIRS]
    if len(agent_dirs) != 1:
        err(f"包根应恰有一个 Agent 目录，现在是：{agent_dirs}")
        return rep
    agent = agent_dirs[0]
    adir = root / agent
    if not NAME_RE.match(agent):
        err(f"Agent 目录名不符合命名规则（小写字母、数字、连字符）：{agent}")

    skills_dir = adir / "skills"
    found = set()
    if not skills_dir.is_dir():
        err("缺少 skills/ 目录")
    else:
        for sp in sorted(skills_dir.iterdir()):
            if not sp.is_dir() or sp.name.startswith(".") or sp.name in SKIP_DIRS:
                continue
            found.add(sp.name)
            sm = sp / "SKILL.md"
            if not sm.exists():
                err(f"Skill 目录缺少 SKILL.md：skills/{sp.name}")
                continue
            fm, _ = frontmatter(sm.read_text("utf-8"))
            if fm is None:
                err(f"SKILL.md 没有 frontmatter：skills/{sp.name}")
                continue
            for k in ("name", "version", "description"):
                if not fm.get(k):
                    err(f"SKILL.md frontmatter 缺少 {k}：skills/{sp.name}")
            if fm.get("name") and fm["name"] != sp.name:
                warn(f"SKILL.md name 与目录名不一致：{fm['name']} vs {sp.name}")
            desc = fm.get("description", "")
            if "不适用" not in desc and "不用于" not in desc and "NOT for" not in desc:
                warn(f"Skill 描述没写不适用场景：skills/{sp.name}")
        if not found:
            err("skills/ 为空")
    missing = skill_ids - found
    if missing:
        err(f"{mname} 里列了但 skills/ 没有的 Skill：{sorted(missing)}")
    unlisted = found - skill_ids
    if unlisted and caps:
        warn(f"skills/ 里有但 {mname} 未列的 Skill：{sorted(unlisted)}")

    if meta.get("type") == "cc":
        entry = adir / "agents" / f"{agent}.md"
        if not entry.exists():
            err(f"cc 形态缺少主入口 {agent}/agents/{agent}.md")
        else:
            fm, body = frontmatter(entry.read_text("utf-8"))
            if fm is None:
                err("主入口没有 frontmatter")
            else:
                for k in ("name", "version", "description"):
                    if not fm.get(k):
                        err(f"主入口 frontmatter 缺少 {k}")
                if fm.get("name") != agent:
                    err(f"主入口 name（{fm.get('name')}）与 Agent 目录名（{agent}）不一致")
                for sec in REQUIRED_SECTIONS:
                    if sec not in body:
                        err(f"主入口缺少必须区块：{sec}")
                if "分派" not in body and "工作方式" not in body:
                    err("主入口缺少任务分派规则")
                for sid in found:
                    if sid not in body:
                        warn(f"主入口路由表未提到 Skill：{sid}")
    elif meta.get("type") == "kc":
        for f in ("config.yaml", "SOUL.md"):
            if not (adir / f).exists():
                err(f"kc 形态缺少 {f}")
        warn("kc 形态只做校验，本系统暂不运行")
    return rep


def _zip_root(names: list[str]) -> str:
    """zip 顶层直接是包根；也兼容多包了一层目录的情况。"""
    tops = {PurePosixPath(n).parts[0] for n in names if n and not n.startswith("__MACOSX")}
    if any(m in tops for m in MANIFEST_NAMES):
        return ""
    if len(tops) == 1:
        only = next(iter(tops))
        if any(f"{only}/{m}" in names for m in MANIFEST_NAMES):
            return only + "/"
    return ""


def safe_extract(zip_path: str | Path, dest: str | Path) -> tuple[Report, Path | None]:
    """校验 zip 本身并解压到 dest，返回（报告，包根）。dest 必须是新的空目录。"""
    rep = Report()
    zp, dest = Path(zip_path), Path(dest)
    if zp.stat().st_size > ZIP_MAX:
        rep.errors.append(f"压缩包 {zp.stat().st_size / 1e6:.1f} MB 超过 50 MB")
        return rep, None
    try:
        zf = zipfile.ZipFile(zp)
    except zipfile.BadZipFile:
        rep.errors.append("不是合法的 zip 文件")
        return rep, None
    with zf:
        infos = [i for i in zf.infolist() if not i.filename.startswith("__MACOSX")]
        if len(infos) > FILES_MAX:
            rep.errors.append(f"文件数 {len(infos)} 超过 {FILES_MAX}")
        if sum(i.file_size for i in infos) > UNZIP_MAX:
            rep.errors.append("解压后超过 200 MB")
        for i in infos:
            n = i.filename
            parts = PurePosixPath(n).parts
            if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or ".." in parts:
                rep.errors.append(f"路径越界，拒绝解压：{n}")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                rep.errors.append(f"包含符号链接：{n}")
            if PurePosixPath(n).name == ".env":
                rep.errors.append(f".env 不得入包：{n}")
        if rep.errors:
            return rep, None
        dest.mkdir(parents=True, exist_ok=False)
        base = dest.resolve()
        for i in infos:
            target = (dest / i.filename).resolve()
            if base not in target.parents and target != base:
                rep.errors.append(f"路径越界，拒绝解压：{i.filename}")
                shutil.rmtree(dest, ignore_errors=True)
                return rep, None
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        root = dest / _zip_root([i.filename for i in infos])
    return rep, root.resolve()
