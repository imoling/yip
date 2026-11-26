"""外部队员（全量目录、安全配置、通用 ACP 引擎、审批与产物）测试。"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from server import db, external, main, runner
from server.workers import registry


def _write_mock_acp(path: Path, *, ask_permission: bool = False, write_file: str = "", sleep_s: float = 0.0) -> str:
    """生成一个标准 ACP (JSON-RPC 2.0 over stdio) 假服务端脚本，不碰真模型和用户目录。"""
    code = textwrap.dedent(f"""\
        import json, os, sys, time
        cwd = os.getcwd()
        def send(obj):
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
            sys.stdout.flush()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            method = msg.get("method")
            rid = msg.get("id")
            if method == "initialize":
                send({{"jsonrpc": "2.0", "id": rid, "result": {{"protocolVersion": 1}}}})
            elif method == "session/new":
                cwd = (msg.get("params") or {{}}).get("cwd") or cwd
                send({{"jsonrpc": "2.0", "id": rid, "result": {{"sessionId": "sess-1"}}}})
            elif method == "session/prompt":
                send({{"jsonrpc": "2.0", "method": "session/update", "params": {{
                    "sessionId": "sess-1",
                    "update": {{"sessionUpdate": "agent_thought_chunk", "content": {{"type": "text", "text": "先分析需求\\n"}}}}
                }}}})
                send({{"jsonrpc": "2.0", "method": "session/update", "params": {{
                    "sessionId": "sess-1",
                    "update": {{"sessionUpdate": "tool_call", "title": "写入结果文件", "status": "completed"}}
                }}}})
                if {ask_permission!r}:
                    send({{"jsonrpc": "2.0", "id": 99, "method": "session/request_permission", "params": {{
                        "sessionId": "sess-1",
                        "toolCall": {{"title": "执行检查脚本", "kind": "exec"}},
                        "options": [
                            {{"optionId": "opt-allow", "kind": "allow_once", "name": "允许"}},
                            {{"optionId": "opt-deny", "kind": "reject_once", "name": "拒绝"}},
                        ]
                    }}}})
                    resp_line = sys.stdin.readline()
                    resp = json.loads(resp_line)
                    chosen = ((resp.get("result") or {{}}).get("outcome") or {{}}).get("optionId")
                    if chosen != "opt-allow":
                        send({{"jsonrpc": "2.0", "method": "session/update", "params": {{
                            "sessionId": "sess-1",
                            "update": {{"sessionUpdate": "agent_message_chunk", "content": {{"type": "text", "text": "操作被拒绝"}}}}
                        }}}})
                        send({{"jsonrpc": "2.0", "id": rid, "result": {{"stopReason": "end_turn"}}}})
                        continue
                if {sleep_s!r}:
                    time.sleep({sleep_s!r})
                if {write_file!r}:
                    with open(os.path.join(cwd, {write_file!r}), "w", encoding="utf-8") as f:
                        f.write("# 外部队员产出\\n\\n测试通过\\n")
                send({{"jsonrpc": "2.0", "method": "session/update", "params": {{
                    "sessionId": "sess-1",
                    "update": {{"sessionUpdate": "agent_message_chunk", "content": {{"type": "text", "text": "已完成外部任务并写入文件。"}}}}
                }}}})
                send({{"jsonrpc": "2.0", "id": rid, "result": {{"stopReason": "end_turn"}}}})
    """)
    path.write_text(code, encoding="utf-8")
    return f"{sys.executable} {path}"


def test_external_catalog_and_security_storage(fresh_db, tmp_path):
    with TestClient(main.app) as c:
        items = c.get("/api/external-agents").json()
        by_id = {x["id"]: x for x in items}
        # 全部本地 ACP 外部队员均已就绪
        for wid in ("ext-agy", "ext-hermes", "ext-codex", "ext-pi", "ext-workbuddy",
                    "ext-grok", "ext-openclaw", "ext-claude-code", "ext-muse"):
            assert by_id[wid]["status"] == "ready"
            assert by_id[wid]["enabled"] is False
            assert by_id[wid]["avatar"] == wid
        # 豆包工作暂未开发
        assert by_id["ext-doubao-work"]["status"] == "planned"
        assert c.put("/api/external-agents/ext-doubao-work", json={"enabled": True}).status_code == 400

        # 未安装/命令不存在时不能虚假上岗
        bad = c.put("/api/external-agents/ext-codex", json={"enabled": True, "command": "/nonexistent/cmd-xyz"})
        assert bad.status_code == 400

        # 保存密钥脱敏与 0600 权限（指定可用命令后才允许上岗）
        r = c.put("/api/external-agents/ext-codex", json={
            "enabled": True,
            "auth_mode": "api_key",
            "api_key": "sk-test-secret-123",
            "command": sys.executable,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["has_key"] is True
        assert body["api_key"] == external.MASK
        cfg_file = db.DATA_DIR / "external_agents.json"
        assert stat.S_IMODE(os.stat(cfg_file).st_mode) == 0o600

        # 传脱敏占位符不覆盖原密钥
        c.put("/api/external-agents/ext-codex", json={"api_key": external.MASK})
        assert external.get_entry("ext-codex")["api_key"] == "sk-test-secret-123"

        # 启用后自动进入队员注册表
        ids = {w.id for w in registry.active()}
        assert "ext-codex" in ids
        assert registry.get("ext-codex").kind == "acp"


def test_acp_worker_end_to_end_and_artifacts(fresh_db, tmp_path):
    mock_cmd = _write_mock_acp(tmp_path / "mock_acp.py", write_file="result.md")
    with TestClient(main.app) as c:
        r = c.put("/api/external-agents/ext-agy", json={"enabled": True, "command": mock_cmd})
        assert r.status_code == 200
        assert r.json()["installed"] is True

        tr = c.post("/api/workers/ext-agy/try", json={"text": "生成一份测试报告"}).json()
        mid, kid = tr["job"], tr["task"]

        # 等待异步任务完成
        for _ in range(60):
            st = c.get(f"/api/tasks/{mid}").json()
            if st["children"][0]["status"] in ("done", "failed"):
                break
            import time
            time.sleep(0.05)

        child = st["children"][0]
        assert child["status"] == "done", st
        assert "已完成外部任务" in (child["result"] or "")
        names = [a["name"] for a in st["artifacts"]]
        assert "result.md" in names


def test_acp_worker_permission_approval(fresh_db, tmp_path):
    mock_cmd = _write_mock_acp(tmp_path / "mock_acp_perm.py", ask_permission=True, write_file="approved.md")
    with TestClient(main.app) as c:
        c.put("/api/external-agents/ext-hermes", json={"enabled": True, "command": mock_cmd})
        tr = c.post("/api/workers/ext-hermes/try", json={"text": "需要审批的外部任务"}).json()
        mid, kid = tr["job"], tr["task"]

        # 等待审批记录出现
        aid = None
        for _ in range(60):
            rows = db.q("SELECT * FROM approvals WHERE task_id=? AND status='pending'", (kid,))
            if rows:
                aid = rows[0]["id"]
                break
            import time
            time.sleep(0.05)
        assert aid is not None, "未触发 ACP 审批抽屉"

        # 放行审批
        res = c.post(f"/api/approvals/{aid}", json={"decision": "allow"})
        assert res.status_code == 200

        # 等待任务跑完
        for _ in range(60):
            st = c.get(f"/api/tasks/{mid}").json()
            if st["children"][0]["status"] in ("done", "failed"):
                break
            import time
            time.sleep(0.05)

        assert st["children"][0]["status"] == "done", st
        assert "approved.md" in [a["name"] for a in st["artifacts"]]


def test_say_mention_pins_specific_worker(fresh_db, tmp_path):
    """在对话框里 @指定队员 后，Yip 直接把整件事派给该队员干活。"""
    mock_cmd = _write_mock_acp(tmp_path / "mock_acp_pin.py", write_file="pinned.md")
    with TestClient(main.app) as c:
        c.put("/api/external-agents/ext-agy", json={"enabled": True, "command": mock_cmd})
        res = c.post("/api/say", json={"text": "@Antigravity 帮我生成指定队员的产物"})
        assert res.status_code == 200
        body = res.json()
        assert body["kind"] == "new"
        assert body["worker_id"] == "ext-agy"
        mid = body["job"]

        for _ in range(80):
            st = c.get(f"/api/tasks/{mid}").json()
            if st["children"] and st["children"][0]["status"] in ("done", "failed"):
                break
            import time
            time.sleep(0.05)

        assert len(st["children"]) == 1
        child = st["children"][0]
        assert child["worker_id"] == "ext-agy"
        assert child["assign_reason"] == "你指定的"
        assert child["status"] == "done", st
        assert "pinned.md" in [a["name"] for a in st["artifacts"]]


def test_external_check_connection_handshake(fresh_db, tmp_path):
    """连通性检查：真实执行 ACP initialize 握手并持久化结果。"""
    mock_cmd = _write_mock_acp(tmp_path / "mock_acp_check.py")
    with TestClient(main.app) as c:
        # 正常握手通过
        c.put("/api/external-agents/ext-agy", json={"command": mock_cmd})
        ok = c.post("/api/external-agents/ext-agy/check", json={}).json()
        assert ok["ok"] is True
        assert "连通正常" in ok["message"]
        assert ok["item"]["checked_ok"] is True

        # 不存在的命令握手失败
        fail = c.post("/api/external-agents/ext-hermes/check", json={"command": "/nonexistent/hermes-xyz"}).json()
        assert fail["ok"] is False
        assert fail["item"]["checked_ok"] is False
