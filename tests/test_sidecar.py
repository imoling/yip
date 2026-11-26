"""桌面壳子进程：令牌校验、health、CORS、--run-script、冻结后的路径。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from server import run
from server.main import app
from server.workers import registry, skill_exec

ROOT = Path(__file__).resolve().parent.parent
PRICE = ROOT / "workers/情报调研员/intel-researcher/demo/search-competitor-price/scripts/main.py"
TOKEN = "t0ken-abc"


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_without_token(monkeypatch):
    monkeypatch.delenv("SA_API_TOKEN", raising=False)
    async with client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.parametrize("kw,code", [
    ({}, 401),
    ({"headers": {"Authorization": "Bearer wrong"}}, 401),
    ({"headers": {"Authorization": f"Bearer {TOKEN}"}}, 200),
    ({"headers": {"authorization": f"bearer {TOKEN}"}}, 200),
    ({"params": {"token": TOKEN}}, 200),
    ({"params": {"token": "wrong"}}, 401),
])
async def test_token_on_get(monkeypatch, kw, code):
    monkeypatch.setenv("SA_API_TOKEN", TOKEN)
    async with client() as c:
        assert (await c.get("/api/health", **kw)).status_code == code
        assert (await c.get("/api/workers", **kw)).status_code == code


async def test_query_token_only_for_get(monkeypatch, fresh_db):
    monkeypatch.setenv("SA_API_TOKEN", TOKEN)
    async with client() as c:
        r = await c.post("/api/tasks", params={"token": TOKEN}, json={"goal": ""})
        assert r.status_code == 401 and r.json()["detail"] == "令牌无效"
        r = await c.post("/api/tasks", headers={"Authorization": f"Bearer {TOKEN}"}, json={"goal": ""})
        assert r.status_code == 400  # 过了令牌，进到接口本身的校验


async def test_static_not_guarded(monkeypatch):
    monkeypatch.setenv("SA_API_TOKEN", TOKEN)
    async with client() as c:
        assert (await c.get("/")).status_code == 200


@pytest.mark.parametrize("origin,allowed", [
    ("tauri://localhost", True), ("http://tauri.localhost", True), ("http://localhost:1420", True),
    ("http://evil.example", False), ("http://localhost:8770", False),
])
async def test_cors(monkeypatch, origin, allowed):
    monkeypatch.setenv("SA_API_TOKEN", TOKEN)
    async with client() as c:
        pre = await c.options("/api/tasks", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        r = await c.get("/api/health", headers={"Origin": origin})
    assert (pre.headers.get("access-control-allow-origin") == origin) is allowed
    assert (r.headers.get("access-control-allow-origin") == origin) is allowed
    assert r.status_code == 401  # 跨域放行不等于免令牌


def _run_script(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "server.run", "--run-script", *args],
                          cwd=cwd, capture_output=True, text=True, timeout=30,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


def test_run_script_matches_python_I():
    args = ["--input", "查定价", "--constraints", json.dumps(["定价只看企业版"], ensure_ascii=False)]
    via = _run_script(str(PRICE), *args, cwd=PRICE.parent)
    direct = subprocess.run([sys.executable, "-I", str(PRICE), *args], capture_output=True, text=True)
    assert via.returncode == 0 and via.stdout == direct.stdout and "企业版" in via.stdout


def test_run_script_exit_codes(tmp_path):
    (tmp_path / "exit3.py").write_text("import sys\nsys.exit(3)\n")
    (tmp_path / "boom.py").write_text("raise ValueError('炸了')\n")
    (tmp_path / "argv.py").write_text("import sys, json\nprint(json.dumps(sys.argv[1:], ensure_ascii=False))\nprint(__name__)\n")
    assert _run_script(str(tmp_path / "exit3.py")).returncode == 3
    r = _run_script(str(tmp_path / "boom.py"))
    assert r.returncode == 1 and "ValueError" in r.stderr
    r = _run_script(str(tmp_path / "argv.py"), "--a", "中 文")
    assert r.stdout.splitlines() == ['["--a", "中 文"]', "__main__"]
    assert _run_script().returncode == 2


def test_run_script_isolated_like_python_I(tmp_path):
    """脚本目录不进 sys.path：同目录的 json.py 不会顶替标准库。"""
    (tmp_path / "json.py").write_text("raise SystemExit('被顶替了')\n")
    (tmp_path / "s.py").write_text("import json\nprint(json.dumps([1]))\n")
    r = _run_script(str(tmp_path / "s.py"), cwd=tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "[1]"


def test_script_cmd(monkeypatch):
    s = Path("/x/main.py")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert skill_exec.script_cmd(s, ["--a"]) == [sys.executable, "-I", "/x/main.py", "--a"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/app/iml-yip-server")
    assert skill_exec.script_cmd(s, ["--a"]) == ["/app/iml-yip-server", "--run-script", "/x/main.py", "--a"]


async def test_frozen_skill_exec_end_to_end(monkeypatch, tmp_path, fresh_db):
    """假装冻结：sys.executable 换成一个转给 `python -m server.run` 的壳，技能照样跑出结果。"""
    shim = tmp_path / "iml-yip-server"
    shim.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT}" exec "{sys.executable}" -m server.run "$@"\n')
    shim.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(shim))
    card = registry.get("intel-researcher")
    skill = next(s for s in card.demos if s.id == "search-competitor-price")
    out = await skill_exec.run(card, skill, "查定价", ["定价只看企业版"], 1)
    assert "企业版" in out and "个人版" not in out and not out.startswith("执行失败")


def test_builtin_dir_frozen(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert registry._builtin_dir() == ROOT / "workers"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert registry._builtin_dir() == tmp_path / "workers"


def test_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SA_DATA_DIR", str(tmp_path / "d"))
    assert run._data_dir() == (tmp_path / "d").resolve()
    monkeypatch.delenv("SA_DATA_DIR")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert run._data_dir() is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert run._data_dir() == Path.home() / "Library/Application Support/cn.imoling.yip"


def test_exits_with_parent(tmp_path):
    """SA_PARENT_PID 指向的进程一结束，服务自己退出。"""
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SA_PARENT_PID": str(parent.pid),
           "SA_DATA_DIR": str(tmp_path), "SA_API_TOKEN": TOKEN}
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "server.run", "--port", str(port)], cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait_health(port)
        parent.kill()
        parent.wait()
        assert srv.wait(timeout=15) is not None
    finally:
        parent.kill()
        srv.kill()


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port, timeout=20):
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", headers={"Authorization": f"Bearer {TOKEN}"},
                          timeout=1, trust_env=False)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("服务没起来")
