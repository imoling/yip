import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CACHE = Path.home() / "Library" / "Caches" / "iml-sa" / "pytest"
os.environ.setdefault("SA_DATA_DIR", str(CACHE))
os.environ["SA_STEP_DELAY"] = "0.05,0.1"
os.environ["SA_WORKSPACE"] = str(CACHE / "workspace")
for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_FAST_MODEL"):
    os.environ.pop(k, None)

from server import db  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.reset_for_tests(tmp_path / "app.db")
    from server.workers import registry
    registry.reload()
    yield tmp_path


def load_cases():
    return [json.loads(l) for l in (ROOT / "tests" / "cases.jsonl").read_text("utf-8").splitlines() if l.strip()]


def seed_context(progress=(0.64, 0.41, 0.30)):
    """标准上下文：三家竞品简报，A 定价 / B 渠道 / C 功能 在跑。"""
    now = db.now()
    db.insert("tasks", id="t_main", parent_id=None, label="", title="三家竞品简报",
              goal="帮我整理一份三家竞品的简报，重点看定价和渠道。", constraints=[], status="running",
              created_at=now, updated_at=now)
    for (tid, label, title, focus), p in zip(
            [("t_a", "A", "竞品定价策略", "定价"), ("t_b", "B", "渠道与代理政策", "渠道"), ("t_c", "C", "产品功能差异", "功能")],
            progress):
        db.insert("tasks", id=tid, parent_id="t_main", label=label, title=title,
                  goal=f"帮我整理一份三家竞品的简报，重点看定价和渠道——这一块只负责「{focus}」", constraints=[],
                  status="running", progress=p, worker_id="intel-researcher", created_at=now, updated_at=now)
    return {"main": "t_main", "A": "t_a", "B": "t_b", "C": "t_c"}
