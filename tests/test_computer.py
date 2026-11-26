"""队员操作电脑：每一步都要问；按键解析；没权限时说清去哪开。真点界面要辅助功能权限，测试里不点。"""
import pytest

from server.tools import Ctx, ToolError, computer, risk

pytestmark = pytest.mark.skipif(not computer.available(), reason="不是 macOS 或没装 pyobjc")


@pytest.mark.parametrize("tool", ["computer_open", "computer_click", "computer_type", "computer_key"])
def test_every_step_asks(tool):
    for policy in ("auto", "allow_all", "ask_writes"):
        assert risk.decide(tool, "exec", policy, []) == "ask", "全部放行也要问"
        assert risk.decide(tool, "exec", policy, ["computer_*"]) == "allow", "这件事里的电脑操作都允许了"
        assert risk.decide(tool, "exec", policy, ["write_file"]) == "ask"
    assert risk.decide("computer_look", "read", "auto") == "allow", "看界面不问"


def test_parse_keys():
    import Quartz
    assert computer.parse_keys("cmd+n") == (45, Quartz.kCGEventFlagMaskCommand)
    assert computer.parse_keys("return") == (36, 0)
    code, flags = computer.parse_keys("cmd+shift+s")
    assert code == 1 and flags & Quartz.kCGEventFlagMaskShift
    with pytest.raises(ToolError):
        computer.parse_keys("cmd+无")


async def test_no_permission_message(monkeypatch):
    monkeypatch.setattr(computer, "trusted", lambda: False)
    with pytest.raises(ToolError, match="辅助功能"):
        await computer.computer_look(Ctx("t", "m", "w", None))


async def test_look_reads_front_app(monkeypatch):
    if not computer.trusted():
        pytest.skip("这台电脑没给辅助功能权限")
    out = await computer.computer_look(Ctx("t-look", "m", "w", None), "Finder")
    assert out.startswith("前台应用：")


async def test_allow_task_grants_whole_computer_group(fresh_db):
    """点「这件事里的电脑操作都允许」：点、打字、按键、打开应用一起放行，后面不再问；发邮件照样每次问。"""
    import httpx
    from server import db
    from server.main import app
    db.insert("tasks", id="m", parent_id=None, label="", title="t", goal="g", status="running",
              created_at=db.now(), updated_at=db.now())
    ids = [db.insert("approvals", task_id="m", main_id="m", run_id="r", worker_id="w", tool=t, args={}, summary=t,
                     risk="exec", status="pending", created_at=db.now()) for t in ("computer_click", "computer_type")]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            db.update("approvals", ids[1], status="pending")  # 启动时会把挂着的作废，重新挂上
            db.update("approvals", ids[0], status="pending")
            r = await c.post(f"/api/approvals/{ids[0]}", json={"decision": "allow_task"})
            assert r.json()["ok"]
    assert db.task("m")["grants"] == ["computer_*"]
    assert db.q1("SELECT status FROM approvals WHERE id=?", (ids[1],))["status"] == "allowed"
    assert risk.decide("computer_key", "exec", "auto", db.task("m")["grants"]) == "allow"
    assert risk.decide("mail_send", "external", "auto", ["computer_*", "*"]) == "ask"
