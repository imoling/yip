"""连接器（MCP）：data/mcp.json 用标准的 mcpServers 格式，能直接粘贴 Claude Desktop / Cursor 的配置。

    {"mcpServers": {
        "高德地图": {"url": "https://mcp.amap.com/mcp?key=…"},
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"]},
        "x": {..., "enabled": false, "requires_approval": false}
    }}

每个连接器一个长驻协程持有会话（anyio 的 cancel scope 要求进出在同一个任务里），第一次用时才连。
工具名 mcp__<连接器>__<工具>，最长 64。配置格式和命名照 OpenWorker（MIT）的 coworker/mcp/。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field

from .. import db
from . import EXTERNAL, READ, Ctx, Tool, ToolError, clip
from .shell import login_path

CONNECT_TIMEOUT = 120  # 第一次用 npx / uvx 要先下载包
# 国内网络下 PyPI 和 npm 官方源很慢：没指定源时默认走镜像
MIRRORS = {"uvx": {"UV_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple"},
           "uv": {"UV_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple"},
           "npx": {"npm_config_registry": "https://registry.npmmirror.com"}}


def _path():
    return db.DATA_DIR / "mcp.json"


def load() -> dict[str, dict]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return dict(json.loads(p.read_text("utf-8")).get("mcpServers") or {})
    except json.JSONDecodeError:
        return {}


def save(servers: dict[str, dict]) -> None:
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(_path(), 0o600)


def transport(cfg: dict) -> str:
    return "http" if cfg.get("url") or cfg.get("type") in ("http", "sse", "streamable-http") else "stdio"


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


@dataclass
class Conn:
    name: str
    cfg: dict
    tools: list = field(default_factory=list)
    session: object = None
    error: str = ""
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    async def _main(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client
        try:
            if transport(self.cfg) == "http":
                cm = streamablehttp_client(self.cfg["url"], headers=self.cfg.get("headers") or None)
                async with cm as (read, write, _):
                    await self._session(ClientSession, read, write)
            else:
                cmd = os.path.basename(str(self.cfg["command"]))
                mirror = {k: v for k, v in MIRRORS.get(cmd, {}).items() if k not in os.environ}
                if mirror:  # 镜像在国内，走代理反而慢
                    hosts = "pypi.tuna.tsinghua.edu.cn,registry.npmmirror.com,cdn.npmmirror.com"
                    for k in ("NO_PROXY", "no_proxy"):
                        mirror[k] = ",".join(x for x in (os.environ.get(k, ""), hosts) if x)
                env = {**os.environ, "PATH": login_path(), **mirror,
                       **{k: str(v) for k, v in (self.cfg.get("env") or {}).items()}}
                params = StdioServerParameters(command=self.cfg["command"], args=[os.path.expanduser(str(a)) for a in self.cfg.get("args") or []],
                                               env=env, cwd=os.path.expanduser(self.cfg["cwd"]) if self.cfg.get("cwd") else None)
                async with stdio_client(params) as (read, write):
                    await self._session(ClientSession, read, write)
        except BaseException as e:  # noqa: BLE001 — 连不上要把原因留给界面
            if not isinstance(e, asyncio.CancelledError):
                self.error = f"{type(e).__name__}: {e}"[:300]
        finally:
            self.session = None
            self.ready.set()

    async def _session(self, ClientSession, read, write) -> None:  # noqa: N803
        async with ClientSession(read, write) as s:
            await s.initialize()
            self.tools = (await s.list_tools()).tools
            self.session = s
            self.error = ""
            self.ready.set()
            await self.stop.wait()


_conns: dict[str, Conn] = {}


async def connect(name: str, cfg: dict | None = None) -> Conn:
    cfg = cfg if cfg is not None else load().get(name)
    if not cfg:
        raise ToolError(f"没有叫 {name} 的连接器")
    c = _conns.get(name)
    if c and c.session is not None and c.cfg == cfg:
        return c
    if c:
        await disconnect(name)
    c = Conn(name, cfg)
    _conns[name] = c
    c.task = asyncio.create_task(c._main())
    try:
        await asyncio.wait_for(c.ready.wait(), CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        c.error = f"{CONNECT_TIMEOUT} 秒没连上"
        await disconnect(name)
    return c


async def disconnect(name: str) -> None:
    c = _conns.pop(name, None)
    if c and c.task:
        c.stop.set()
        try:
            await asyncio.wait_for(c.task, 5)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            c.task.cancel()


async def shutdown() -> None:
    for n in list(_conns):
        await disconnect(n)


def human(err: str) -> str:
    e = err or ""
    if "FileNotFoundError" in e or "No such file" in e:
        return "找不到启动命令，看看命令是否装好"
    if "ConnectError" in e or "ConnectTimeout" in e:
        return "地址连不上"
    if "401" in e or "403" in e:
        return "没有权限，检查密钥或令牌"
    if "秒没连上" in e:
        return f"{e}，第一次用要先下载，过一会儿再点「测试」"
    return "连接失败"


async def test(name: str, cfg: dict) -> dict:
    c = await connect(name, cfg)
    if c.session is None:
        return {"ok": False, "error": human(c.error), "detail": c.error}
    return {"ok": True, "tools": [{"name": t.name, "description": (t.description or "")[:200]} for t in c.tools]}


def _text(res) -> str:
    parts = []
    for item in getattr(res, "content", []) or []:
        t = getattr(item, "text", None)
        parts.append(t if t is not None else f"[{getattr(item, 'type', '内容')}]")
    sc = getattr(res, "structuredContent", None)
    if not parts and sc:
        parts.append(json.dumps(sc, ensure_ascii=False))
    return "\n".join(parts)


async def tools_for(allowed: list[str] | None = None) -> list[Tool]:
    """已启用的连接器的全部工具；allowed 为 None 时全给，否则只给勾选的连接器。连不上的跳过。"""
    out: list[Tool] = []
    for name, cfg in load().items():
        if cfg.get("enabled") is False or (allowed is not None and name not in allowed):
            continue
        c = await connect(name, cfg)
        if c.session is None:
            continue
        risk = EXTERNAL if cfg.get("requires_approval", True) else READ
        for t in c.tools:
            tool_name = f"mcp__{safe(name)}__{safe(t.name)}"[:64]

            async def run(ctx: Ctx, _c=c, _t=t.name, **args) -> str:
                if _c.session is None:
                    raise ToolError(f"连接器 {_c.name} 断开了")
                res = await _c.session.call_tool(_t, args)
                text = _text(res)
                if getattr(res, "isError", False):
                    raise ToolError(f"{_c.name} 报错：{text[:500]}")
                return clip(text, 12000)

            out.append(Tool(tool_name, f"[{name}] {t.description or t.name}",
                            t.inputSchema or {"type": "object", "properties": {}}, run, risk,
                            lambda a, _n=name, _t=t.name: f"{_n} · {_t}", f"{name}"))
    return out


def status() -> list[dict]:
    out = []
    for name, cfg in load().items():
        c = _conns.get(name)
        out.append({"name": name, "config": cfg, "transport": transport(cfg), "enabled": cfg.get("enabled", True),
                    "requires_approval": cfg.get("requires_approval", True),
                    "connected": bool(c and c.session is not None), "tools": len(c.tools) if c else 0,
                    "error": human(c.error) if c and c.error else ""})
    return out
