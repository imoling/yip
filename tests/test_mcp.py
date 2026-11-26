"""连接器：用本地 stdio 小服务验证连接、列工具、调用、命名和风险级别。"""
import sys
from pathlib import Path

from server.tools import EXTERNAL, READ, Ctx
from server.tools import mcp

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_echo.py")


async def test_stdio_roundtrip(fresh_db, tmp_path):
    mcp.save({"回声": {"command": sys.executable, "args": [SERVER]},
              "关掉的": {"command": sys.executable, "args": [SERVER], "enabled": False}})
    r = await mcp.test("回声", mcp.load()["回声"])
    assert r["ok"] and {t["name"] for t in r["tools"]} == {"echo", "add"}
    tools = {t.name: t for t in await mcp.tools_for()}
    assert "mcp_____echo" in tools or any(n.endswith("__echo") for n in tools)
    echo = next(t for n, t in tools.items() if n.endswith("__echo"))
    assert echo.risk == EXTERNAL and len(echo.name) <= 64
    out = await echo.run(Ctx("t", "m", "w", tmp_path), text="你好")
    assert "回声：你好" in out
    add = next(t for n, t in tools.items() if n.endswith("__add"))
    assert "5" in await add.run(Ctx("t", "m", "w", tmp_path), a=2, b=3)
    assert all("关掉的" not in t.description for t in tools.values())
    st = {s["name"]: s for s in mcp.status()}
    assert st["回声"]["connected"] and st["回声"]["tools"] == 2 and not st["关掉的"]["connected"]
    await mcp.shutdown()


async def test_no_approval_flag_and_bad_command(fresh_db):
    mcp.save({"免问": {"command": sys.executable, "args": [SERVER], "requires_approval": False}})
    tools = await mcp.tools_for()
    assert tools and all(t.risk == READ for t in tools)
    r = await mcp.test("坏的", {"command": "/no/such/binary"})
    assert not r["ok"] and r["error"]
    await mcp.shutdown()
