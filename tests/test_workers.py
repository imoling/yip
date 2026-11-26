"""数字员工包：校验、导入、能力匹配、表单建员工。"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from server.workers import author, match, registry, validate

ROOT = Path(__file__).resolve().parent.parent


def test_builtins_use_library_skills(fresh_db):
    """内置队员不带空壳技能：真技能从技能库来，demo/ 只是没配模型时的演示数据。"""
    from server.tools import skills as t_skills
    lib = {s.id for s in t_skills.library()}
    assert {"web-game", "research-report", "outline-ppt", "official-notice", "data-table"} <= lib
    cards = [c for c in registry.all_cards() if c.source == "builtin"]
    assert len(cards) == 14
    for c in cards:
        assert not c.skills and c.demos, c.id
        mine = t_skills.for_worker(c, None)
        assert mine and all(s.source == "builtin" for s in mine), c.id


def test_registry_cards(fresh_db):
    names = {c.id: c for c in registry.all_cards()}
    assert {"intel-researcher", "data-analyst", "report-writer", "general-assistant"} <= set(names)
    c = names["intel-researcher"]
    assert c.inputs and c.deliverable and all(s.has_script for s in c.demos)


def test_skill_script_honors_constraint():
    script = ROOT / "workers/情报调研员/intel-researcher/demo/search-competitor-price/scripts/main.py"
    out = subprocess.run([sys.executable, "-I", str(script), "--input", "查定价",
                          "--constraints", json.dumps(["定价只看企业版"], ensure_ascii=False)],
                         capture_output=True, text=True).stdout
    assert "企业版" in out and "个人版" not in out


@pytest.mark.parametrize("title,goal,wid", [
    ("竞品定价策略", "这一块只负责「定价」", "intel-researcher"),
    ("渠道与代理政策", "这一块只负责「渠道」", "intel-researcher"),
    ("产品功能差异", "比对三家的功能清单", "data-analyst"),
    ("服务政策", "查售后和 SLA", "intel-researcher"),
    ("出对照表", "把数据整理成对照表", "data-analyst"),
    ("写一页简报", "起草给领导的简报", "report-writer"),
    ("订会议室", "下周二订个会议室", "meeting-secretary"),
    ("订机票", "帮我看看下周二去北京的机票", "general-assistant"),
])
def test_rule_match(fresh_db, title, goal, wid):
    m = match.rule_match({"title": title, "goal": goal}, registry.active())
    assert m["worker_id"] == wid, m
    assert m["reason"]


def _zip(tmp_path, files: dict, name="p.zip"):
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return zp


def test_import_rejects_bad_zips(fresh_db, tmp_path):
    rep, card = author.import_zip(_zip(tmp_path, {"x/agents/x.md": "hi"}, "a.zip"))
    assert not rep.ok and any("yip.json" in e for e in rep.errors) and card is None
    rep, _ = author.import_zip(_zip(tmp_path, {"yip.json": "{}", "../evil.py": "x"}, "b.zip"))
    assert any("越界" in e for e in rep.errors)
    rep, _ = author.import_zip(_zip(tmp_path, {"yip.json": "{}", "x/.env": "K=1"}, "c.zip"))
    assert any(".env" in e for e in rep.errors)


def test_import_valid_zip(fresh_db, tmp_path):
    pkg = tmp_path / "pkg"
    author.build({
        "name": "条款核对员", "id": "clause-checker", "introduction": "核对合同条款并标出差异",
        "personality": ["只引原文", "风险标清", "结论先行"], "capability": "条款核对",
        "skills": [{"name": "条款比对", "id": "clause-diff", "description": "比对条款差异", "not_for": "出法律意见"}],
    }, pkg)
    zp = tmp_path / "clause-checker.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(pkg).as_posix())
    rep, card = author.import_zip(zp)
    assert rep.ok, rep.errors
    assert card.id == "clause-checker" and card.source == "imported" and not card.enabled
    assert card.id not in {c.id for c in registry.active()}
    registry.set_enabled(card.id, True)
    assert card.id in {c.id for c in registry.active()}
    rep2, _ = author.import_zip(zp)
    assert any("同名" in e for e in rep2.errors)


def test_form_save_creates_valid_worker(fresh_db):
    form = {"name": "合同审核员", "id": "contract-reviewer", "introduction": "审合同条款，标风险，不出法律意见",
            "personality": ["只引原文", "风险标清"], "capability": "合同审核",
            "skills": [{"name": "条款比对", "description": "比对合同条款和范本的差异", "not_for": "出法律意见"},
                       {"name": "风险标注", "description": "标出合同里的风险条款", "not_for": "判断是否违约"}],
            "inputs": "合同文本", "deliverable": "风险条款清单"}
    rep, card = author.save(form)
    assert rep.ok, rep.errors
    assert card.enabled and len(card.skills) == 2 and len(card.personality) == 3
    m = match.rule_match({"title": "合同风险", "goal": "看下这三家的合同条款有什么风险"}, registry.active())
    assert m["worker_id"] == "contract-reviewer"
    assert registry.remove("contract-reviewer") and not registry.get("contract-reviewer")


@pytest.mark.parametrize("title,goal,wid", [
    ("会议纪要", "把今天周会的记录整理成纪要，列出待办", "meeting-secretary"),
    ("写请示", "按公文格式起草一份请示，申请采购两台服务器", "document-clerk"),
    ("本周周报", "把这几条整理成本周周报", "report-assistant"),
    ("做 PPT", "做一份 8 页的汇报 PPT", "ppt-designer"),
    ("合并 PDF", "把这三个 PDF 合并成一个", "file-keeper"),
])
def test_office_members_match(fresh_db, title, goal, wid):
    assert match.rule_match({"title": title, "goal": goal}, registry.active())["worker_id"] == wid
