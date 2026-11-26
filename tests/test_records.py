"""记录页：只列做完、没做成、取消的事，一件事一行；能搜、能筛，交的文件只算交给主人的那几个。"""
import httpx

from server import db, events
from server.main import app


def _job(tid, title, status, worker="report-writer", verdict=None, at=1):
    db.insert("tasks", id=tid, parent_id=None, label="", title=title, goal=title, status=status,
              note="服务重启，已中断" if status == "failed" else "", verdict=verdict, created_at=at, updated_at=at)
    db.insert("tasks", id=f"{tid}-a", parent_id=tid, label="A", title="写", goal="写", status=status,
              worker_id=worker, created_at=at, updated_at=at)


async def test_records(fresh_db):
    _job("t1", "党代会会议通知", "done", "document-clerk", at=3)
    _job("t2", "每日邮件整理脚本", "failed", "developer", at=2)
    _job("t3", "周报", "done", verdict={"pass": False, "problems": ["少了下周计划"]}, at=1)
    a = db.insert("artifacts", task_id="t1-a", main_id="t1", worker_id="document-clerk", path="/ws/通知.docx",
                  name="通知.docx", kind="doc", size=10, preview="", created_at=3)
    db.insert("artifacts", task_id="t1-a", main_id="t1", worker_id="document-clerk", path="/ws/A-笔记.md",
              name="A-笔记.md", kind="doc", size=10, preview="", created_at=3)
    events.message("t1", "assistant", "做完了", final=True, artifact_id=a, files=[])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            _job("t4", "正在做的", "running", at=4)  # 服务起来以后才建，免得启动时被当成中断
            rows = (await c.get("/api/records")).json()
            assert [r["title"] for r in rows] == ["党代会会议通知", "每日邮件整理脚本", "周报"], "正在做的不进记录，新的在前"
            assert rows[0]["files"] == 1 and rows[0]["first_file"] == "通知.docx", "笔记不算交付"
            assert rows[0]["workers"] == ["document-clerk"] and rows[2]["problems"] == 1
            assert [r["id"] for r in (await c.get("/api/records", params={"status": "failed"})).json()] == ["t2"]
            assert [r["id"] for r in (await c.get("/api/records", params={"q": "通知.docx"})).json()] == ["t1"], "按文件名搜"
            assert [r["id"] for r in (await c.get("/api/records", params={"q": "开发工程师"})).json()] == ["t2"], "按队员搜"
            assert [r["id"] for r in (await c.get("/api/records", params={"before": 3})).json()] == ["t2", "t3"]
