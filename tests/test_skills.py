"""技能库：标准 SKILL.md 格式，内置 + 主人装的两层；从文件夹、zip、GitHub 装；队员从库里勾，Yip 在对话里也能装、能分配。"""
import io
import zipfile

import pytest

from server import setup
from server.tools import Ctx, files
from server.tools import skills as t_skills
from server.workers import registry


def _skill(d, sid="weekly-report", desc="每周五把要点整理成周报；不适用：日报", extra=None):
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {sid}\ndescription: {desc}\n---\n\n# 周报整理\n\n## 怎么做\n1. 读本周记录\n", "utf-8")
    for rel, text in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text, "utf-8")
    return d


async def test_install_from_folder_and_give(fresh_db, tmp_path):
    src = _skill(tmp_path / "src" / "weekly-report", extra={"references/模板.md": "# 周报模板"})
    (src / "outside").symlink_to(tmp_path)  # 软链接不带进来
    assert await t_skills.install(str(src)) == ["weekly-report"]
    s = next(x for x in t_skills.library() if x.id == "weekly-report")
    assert s.source == "global" and s.name == "周报整理"
    assert not (t_skills.global_dir() / "weekly-report" / "outside").exists()
    with pytest.raises(ValueError, match="已经装过"):
        await t_skills.install(str(src))
    assert await t_skills.install(str(src), replace=True) == ["weekly-report"]

    out = setup.give_skill("报告撰写员", "weekly-report", True)
    assert "会用" in out
    card = registry.get("report-writer")
    mine = t_skills.for_worker(card, registry.prefs("report-writer")["skills"])
    assert "weekly-report" in [k.id for k in mine] and "consulting-analysis" in [k.id for k in mine], "默认的还在"


async def test_install_folder_of_skills_and_zip(fresh_db, tmp_path):
    root = tmp_path / "many"
    _skill(root / "a-one", "a-one")
    _skill(root / "b-two", "b-two")
    (root / "not-a-skill").mkdir()
    assert await t_skills.install(str(root)) == ["a-one", "b-two"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pack/c-three/SKILL.md", "---\nname: c-three\ndescription: 第三个\n---\n# 三\n")
    zp = tmp_path / "s.zip"
    zp.write_bytes(buf.getvalue())
    assert await t_skills.install(str(zp)) == ["c-three"]
    with pytest.raises(ValueError, match="description"):
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_text("# 没有开头\n", "utf-8")
        await t_skills.install(str(bad))


async def test_install_from_github(fresh_db, tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("repo-main/README.md", "x")
        z.writestr("repo-main/skills/pdf-tool/SKILL.md", "---\nname: pdf-tool\ndescription: 处理 PDF\n---\n# PDF\n")
    seen = {}

    class Resp:
        status_code = 200
        content = buf.getvalue()

    class Client:
        def __init__(self, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url):
            seen["url"] = url
            return Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert await t_skills.install("https://github.com/someone/repo/tree/main/skills/pdf-tool") == ["pdf-tool"]
    assert seen["url"] == "https://github.com/someone/repo/archive/main.zip"
    assert await t_skills.install("https://github.com/someone/repo", replace=True) == ["pdf-tool"], "整个仓库：找 skills/ 下面的"


async def test_member_reads_skill_files(fresh_db, tmp_path):
    src = _skill(tmp_path / "src" / "weekly-report", extra={"references/模板.md": "# 周报模板"})
    await t_skills.install(str(src))
    card = registry.get("report-writer")
    skills = t_skills.for_worker(card, ["weekly-report"])
    tools = {t.name: t for t in t_skills.tools_for(card, skills)}
    ctx = Ctx("t", "t", "w", tmp_path / "ws")
    doc = await tools["load_skill"].run(ctx, "weekly-report")
    assert "references/模板.md" in doc, "告诉队员技能目录里有什么"
    ref = t_skills.global_dir() / "weekly-report" / "references" / "模板.md"
    assert "周报模板" in await files.read_file(ctx, str(ref)), "技能目录只读放行"
    with pytest.raises(Exception):
        await files.write_file(ctx, str(ref), "改掉")
    assert "## 你会的技能" in t_skills.catalog(skills)


async def test_chat_tools(fresh_db, tmp_path):
    src = _skill(tmp_path / "x" / "weekly-report")
    out = await setup.call("install_skill", {"source": str(src), "give": ["通用助理"]})
    assert "装好了：weekly-report" in out and "通用助理会用 weekly-report" in out
    lst = await setup.call("list_skills", {})
    assert "web-game（做网页小游戏，内置）" in lst and "weekly-report" in lst
    assert "没装上" in await setup.call("install_skill", {"source": str(tmp_path / "nope")})
    assert "没有叫" in await setup.call("give_skill", {"member": "谁", "skill": "web-game", "on": True})
