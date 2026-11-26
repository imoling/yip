"""把一个队员包解析成能力卡片 WorkerCard。"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .validate import SKIP_DIRS, frontmatter, manifest_path


@dataclass
class Skill:
    id: str
    name: str
    description: str
    group: str = ""
    has_script: bool = False
    doc: str = ""          # SKILL.md 全文
    dir: str = ""

    @property
    def match_text(self) -> str:
        """用来判断「这个技能对不对口」的文字：去掉「不适用」那几行，免得被反向提到的词带偏。"""
        keep = [ln for ln in self.doc[:600].splitlines() if "不适用" not in ln and "不用于" not in ln]
        return f"{self.name} {self.description} " + " ".join(keep)


@dataclass
class WorkerCard:
    id: str
    name: str
    version: str = "1.0.0"
    introduction: str = ""
    personality: list[str] = field(default_factory=list)
    capabilities: list[dict] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    demos: list[Skill] = field(default_factory=list)   # 内置队员的离线演示数据（demo/），没配模型时用，不算技能
    inputs: str = ""
    deliverable: str = ""
    prompt: str = ""        # 主入口 md 正文，作为 system prompt
    source: str = "builtin"  # builtin / custom / imported
    enabled: bool = True
    root: str = ""
    kind: str = "cc"
    team: str = ""          # 小队：来自 workers/_teams.json 或偏好，界面按它分组

    def summary(self) -> dict:
        """给匹配模型看的能力卡片摘要。"""
        return {
            "id": self.id, "name": self.name, "team": self.team, "introduction": self.introduction,
            "capabilities": [
                {"name": c.get("name"), "description": c.get("description"),
                 "skills": [s.get("name") for s in c.get("skills", [])]} for c in self.capabilities
            ],
            "inputs": self.inputs, "deliverable": self.deliverable,
        }

    def public(self) -> dict:
        d = asdict(self)
        d.pop("prompt")
        d.pop("demos")
        for s in d["skills"]:
            s.pop("doc"); s.pop("dir")
        return d

    def text(self) -> str:
        parts = [self.name, self.introduction, self.inputs, self.deliverable]
        for c in self.capabilities:
            parts += [c.get("name", ""), c.get("description", "")]
            for s in c.get("skills", []):
                parts += [s.get("name", ""), s.get("description", "")]
        return " ".join(p for p in parts if p)


def section(body: str, title: str) -> str:
    m = re.search(rf"^##\s*{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S)
    return m.group(1).strip() if m else ""


def load(root: str | Path, source: str = "builtin") -> WorkerCard:
    root = Path(root)
    mj = manifest_path(root)
    if mj is None:
        raise FileNotFoundError(f"包根缺少 yip.json：{root}")
    meta = json.loads(mj.read_text("utf-8"))
    agent_dir = next(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name not in SKIP_DIRS)
    aid = agent_dir.name
    body = ""
    entry = agent_dir / "agents" / f"{aid}.md"
    if entry.exists():
        _, body = frontmatter(entry.read_text("utf-8"))
    skills, demos = [], []
    for c in meta.get("capabilities") or []:
        for s in c.get("skills") or []:
            for sub, bucket in (("skills", skills), ("demo", demos)):
                sdir = agent_dir / sub / s["id"]
                if not (sdir / "SKILL.md").exists():
                    continue
                bucket.append(Skill(
                    id=s["id"], name=s.get("name", s["id"]), description=s.get("description", ""),
                    group=c.get("name", ""), has_script=(sdir / "scripts" / "main.py").exists(),
                    doc=(sdir / "SKILL.md").read_text("utf-8"), dir=str(sdir),
                ))
    return WorkerCard(
        id=aid, name=meta.get("name", aid), version=meta.get("version", "1.0.0"),
        introduction=meta.get("introduction", ""), personality=meta.get("personality") or [],
        capabilities=meta.get("capabilities") or [], skills=skills, demos=demos,
        inputs=section(body, "需要的输入"), deliverable=section(body, "交付标准"),
        prompt=body.strip(), source=source, root=str(root), kind=meta.get("type", "cc"),
    )
