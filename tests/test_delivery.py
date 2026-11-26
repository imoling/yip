"""交付：命令生成的文件要记成产物、点名要的格式要交真文件、一个人干完的活直接交付。"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from server import db, graph, verify
from server.main import app


@pytest.fixture
async def client(fresh_db):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c


def _docx(path: Path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    d = Document()
    t = d.add_paragraph()
    t.add_run("关于召开党代会的通知").bold = True
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("各党支部：定于 2026 年 11 月 20 日召开党代会。")
    d.save(str(path))


async def test_shell_records_generated_files(fresh_db, tmp_path):
    from server.tools.shell import run_shell
    mid = db.new_id()
    db.insert("tasks", id=mid, parent_id=None, label="", title="通知", goal="写通知", constraints=[], status="running",
              note="", thread_id=f"main:{mid}", created_at=db.now(), updated_at=db.now())
    ctx = SimpleNamespace(workspace=tmp_path, task_id=mid, main_id=mid, worker_id=None)
    (tmp_path / "通知").mkdir()
    out = await run_shell(ctx, f"{sys.executable} -c \"open('通知/会议通知.docx','wb').write(b'x')\"")
    assert "会议通知.docx" in out
    assert [a["name"] for a in db.q("SELECT name FROM artifacts WHERE main_id=?", (mid,))] == ["会议通知.docx"]


def test_format_gaps_and_office_check(tmp_path):
    goal = "写一份会议通知，给我一份 word 文件"
    py = tmp_path / "gen.py"
    py.write_text("print(1)")
    assert verify.format_gaps(goal, [py]) and "Word" in verify.format_gaps(goal, [py])[0]
    doc = tmp_path / "通知.docx"
    _docx(doc)
    assert verify.format_gaps(goal, [py, doc]) == []
    assert asyncio.run(verify.check_office(doc)) == []
    bad = tmp_path / "坏.docx"
    bad.write_bytes(b"not a zip")
    assert "打不开" in asyncio.run(verify.check_office(bad))[0]


def test_docview_keeps_title_centered(tmp_path):
    from server import docview
    doc = tmp_path / "通知.docx"
    _docx(doc)
    h = docview.render(doc)
    assert '<p class="c"><b>关于召开党代会的通知</b></p>' in h and "2026 年 11 月 20 日" in h


def test_deliverables_put_docx_first_and_drop_scripts():
    made = [{"id": 1, "name": "gen.py", "path": "/x/gen.py"}, {"id": 2, "name": "A-说明.md", "path": "/x/A-说明.md"},
            {"id": 3, "name": "会议通知.docx", "path": "/x/会议通知.docx"}]
    assert [a["id"] for a in graph.deliverables(made, "给我一份 Word")] == [3, 2]
    web = [{"id": 1, "name": "app.js", "path": "/x/app.js"}, {"id": 2, "name": "index.html", "path": "/x/index.html"}]
    assert [a["id"] for a in graph.deliverables(web, "做个网页")] == [2, 1]


async def test_single_member_delivers_directly(client, monkeypatch):
    monkeypatch.setattr(graph, "rule_plan", lambda goal: [{"title": "起草", "goal": "起草通知", "steps": 3, "after": []}])
    mid = (await client.post("/api/tasks", json={"goal": "写一份通知 " + str(time.time())})).json()["id"]
    t0 = time.monotonic()
    while db.task(mid)["status"] != "done" and time.monotonic() - t0 < 30:
        await asyncio.sleep(0.05)
    assert db.task(mid)["status"] == "done"
    final = db.q("SELECT * FROM messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (mid,))[0]
    kid = db.children(mid)[0]
    own = db.q("SELECT id FROM artifacts WHERE task_id=?", (kid["id"],))
    if own:  # 队员交了文件：直接交付，不再多一份汇总文档
        assert not final["meta"].get("artifact_id") and final["meta"]["files"]
        assert not db.q("SELECT id FROM artifacts WHERE task_id=?", (mid,))
    else:  # 只回了文字：照样落一份文档，内容不丢
        assert final["meta"].get("artifact_id")
    assert final["content"].startswith("做完了")


async def test_model_call_has_total_deadline_and_retries(monkeypatch):
    """DeepSeek 发空行保活时 httpx 不会超时：整次调用要有总时长，卡住一次就重发。"""
    from server import llm
    calls = []

    async def once(s, body, timeout, retry_without=None):
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(10)
        return {"choices": [{"message": {"content": "好", "tool_calls": []}}]}
    monkeypatch.setattr(llm, "_post_once", once)
    monkeypatch.setattr(llm, "_target", lambda fast, override: {"model": "m", "url": "u", "headers": {}})
    t0 = time.monotonic()
    r = await llm.chat_tools([{"role": "user", "content": "hi"}], [], timeout=0.2)
    assert r["content"] == "好" and len(calls) == 2 and time.monotonic() - t0 < 7


def test_main_doc_skips_notes():
    arts = [{"id": 1, "name": "notes_epl.md", "path": __file__, "size": 9000},
            {"id": 2, "name": "C-汇总成文档.md", "path": __file__, "size": 3000},
            {"id": 3, "name": "Arsenal_战绩汇总.md", "path": __file__, "size": 5000}]
    for a in arts:
        a["path"] = a["path"].replace("test_delivery.py", a["name"])
    import shutil
    for a in arts:
        shutil.copy(__file__, a["path"])
    try:
        assert graph.main_doc(arts)["id"] == 3
        assert graph.main_doc(arts[:2])["id"] == 2, "只有笔记和 A-xxx.md 时给 A-xxx.md"
        assert graph.main_doc(arts[:1]) is None
    finally:
        for a in arts:
            Path(a["path"]).unlink(missing_ok=True)


def test_human_plan_parallel_then_relay():
    L = lambda l, w, t, a=[]: {"label": l, "worker": w, "title": t, "after": a}  # noqa: E731
    say = graph.human_plan([L("A", "通用助理", "英超数据"), L("B", "通用助理", "杯赛数据"), L("C", "报告撰写员", "写报告", ["A", "B"])])
    assert say == "好，让通用助理先做英超数据和杯赛数据，报告撰写员接着写报告，弄好叫你"


async def test_relay_delivers_only_final_report(fresh_db, tmp_path, monkeypatch):
    """接力的最后一个人写好了报告：只交这一份，前面的人的笔记不交，Yip 也不再写第二份。"""
    monkeypatch.setattr(graph.settings, "workspace", lambda: tmp_path)
    mid, a, b = db.new_id(), db.new_id(), db.new_id()
    now = db.now()
    db.insert("tasks", id=mid, parent_id=None, label="", title="阿森纳战绩", goal="了解阿森纳这赛季的战绩",
              constraints=[], status="running", created_at=now, updated_at=now)
    db.insert("tasks", id=a, parent_id=mid, label="A", title="收集数据", goal="收集", status="done", result="收集好了",
              worker_id="general-assistant", created_at=now, updated_at=now)
    db.insert("tasks", id=b, parent_id=mid, label="B", title="写报告", goal="写", status="done", result="写好了",
              prereqs=[a], worker_id="report-writer", created_at=now, updated_at=now)
    folder = tmp_path / "阿森纳战绩"
    folder.mkdir()
    for tid, name, body in [(a, "notes_epl.md", "笔记"), (b, "阿森纳战绩汇总.md", "# 汇总\n9 胜 1 平 1 负")]:
        (folder / name).write_text(body, "utf-8")
        graph.artifacts.record(SimpleNamespace(task_id=tid, main_id=mid, worker_id=None), folder / name)
    await graph.summarize({"main_id": mid, "goal": "", "constraints": [], "subtasks": [], "artifacts": [], "memories": []})
    m = db.q("SELECT * FROM messages WHERE task_id=? ORDER BY id DESC LIMIT 1", (mid,))[0]
    rep = db.q1("SELECT * FROM artifacts WHERE id=?", (m["meta"]["artifact_id"],))
    assert rep["name"] == "阿森纳战绩汇总.md" and m["meta"]["files"] == []
    assert not (folder / "阿森纳战绩.md").exists(), "不再多写一份汇总"
    assert "详细的在报告里" in m["content"]


async def test_image_delivery_dedupes_and_hides_member_notes(fresh_db, tmp_path, monkeypatch):
    """截图交了两份（两个文件夹各一份）只交一份；交的是图片时，队员的 A-xxx.md 不列。"""
    monkeypatch.setattr(graph.settings, "workspace", lambda: tmp_path)
    mid, a = db.new_id(), db.new_id()
    now = db.now()
    db.insert("tasks", id=mid, parent_id=None, label="", title="百度首页截图", goal="打开 baidu.com 截个图给我",
              constraints=[], status="running", created_at=now, updated_at=now)
    db.insert("tasks", id=a, parent_id=mid, label="A", title="截屏", goal="截屏", status="done", result="截好了",
              worker_id="developer", created_at=now, updated_at=now)
    for folder, name, body in [("百度首页截图", "baidu.png", b"PNG1"), ("百度全屏截图", "baidu.png", b"PNG1"),
                               ("百度首页截图", "A-截屏.md", "说明".encode())]:
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / folder / name).write_bytes(body)
        graph.artifacts.record(SimpleNamespace(task_id=a, main_id=mid, worker_id=None), tmp_path / folder / name)
    await graph.summarize({"main_id": mid, "goal": "", "constraints": [], "subtasks": [], "artifacts": [], "memories": []})
    m = db.q("SELECT * FROM messages WHERE task_id=? ORDER BY id DESC LIMIT 1", (mid,))[0]
    files = [db.q1("SELECT * FROM artifacts WHERE id=?", (i,)) for i in m["meta"]["files"]]
    assert [f["path"] for f in files] == [str(tmp_path / "百度首页截图" / "baidu.png")]
    assert not m["meta"].get("artifact_id"), "不再列队员的说明，也不另写汇总"


def test_note_names(fresh_db):
    from server import artifacts
    assert artifacts.is_note("整理成果-报告撰写员.md") and artifacts.is_note("A-整理成果.md")
    assert artifacts.is_note("做 PPT-PPT设计师.md"), "队员名里的空格去掉"
    assert not artifacts.is_note("智能眼镜调研-2026-11.md") and not artifacts.is_note("会议纪要.docx")
