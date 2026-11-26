"""命令行：在工作文件夹里跑一条命令，超时 120 秒，输出截断。

从「访达」打开的应用拿不到终端里的 PATH，第一次用时向登录 shell 要一份。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from .. import artifacts
from . import EXEC, Ctx, Tool, ToolError, clip, obj

_PATH: str | None = None


def login_path() -> str:
    global _PATH
    if _PATH is None:
        try:
            shell = os.environ.get("SHELL") or "/bin/zsh"
            out = subprocess.run([shell, "-lc", "printf %s \"$PATH\""], capture_output=True, text=True, timeout=5)
            _PATH = out.stdout.strip() or os.environ.get("PATH", "")
        except Exception:  # noqa: BLE001
            _PATH = os.environ.get("PATH", "")
        extra = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
                 "/Applications/LibreOffice.app/Contents/MacOS"]  # soffice：技能里转格式、重算公式要用
        have = _PATH.split(":")
        # pyenv 和 uv 一般写在交互式 shell 的配置里，登录 shell 拿不到：放到最前面，跟主人在终端里用的是同一个 python
        home = Path.home()
        front = [str(d) for d in (home / ".pyenv" / "shims", home / ".local" / "bin") if d.is_dir()]
        have = [p for p in have if p not in front]
        _PATH = ":".join(front + have + [p for p in extra if p not in have])
    return _PATH


_NODE: str | None = None


def node_path() -> str:
    """全局装的 npm 包（docx、pptxgenjs…）：技能里的 Node 脚本直接 require 就能找到。"""
    global _NODE
    if _NODE is None:
        try:
            out = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=5,
                                 env={**os.environ, "PATH": login_path()})
            _NODE = out.stdout.strip()
        except Exception:  # noqa: BLE001
            _NODE = ""
    return _NODE


SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv", "附件"}


def _snapshot(root: Path, limit: int = 4000) -> dict[str, float]:
    """工作文件夹里（三层以内）文件的修改时间；文件太多就只看前 limit 个。"""
    out: dict[str, float] = {}
    for dirpath, dirs, files in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS and depth < 3]
        for f in files:
            if f.startswith("."):
                continue
            p = os.path.join(dirpath, f)
            try:
                out[p] = os.stat(p).st_mtime
            except OSError:
                pass
            if len(out) >= limit:
                return out
    return out


async def run_shell(ctx: Ctx, command: str, timeout: int = 120) -> str:
    if not (command or "").strip():
        raise ToolError("命令是空的")
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": login_path()}
    if node_path():
        env["NODE_PATH"] = ":".join(x for x in (os.environ.get("NODE_PATH", ""), node_path()) if x)
    before = _snapshot(ctx.workspace)
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(ctx.workspace), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1, min(int(timeout or 120), 600)))
    except asyncio.TimeoutError:
        proc.kill()
        raise ToolError(f"超过 {timeout} 秒没跑完，已停掉") from None
    text = out.decode("utf-8", errors="replace")
    made = [p for p, t in _snapshot(ctx.workspace).items() if before.get(p) != t][:20]
    for p in made:  # 命令生成的文件（比如脚本写出的 .docx）也算产物，交付时才找得到
        artifacts.record(ctx, Path(p))
    tail = ("\n新文件 / 改过的文件：" + "、".join(str(Path(p).relative_to(ctx.workspace)) for p in made)) if made else ""
    return clip(f"退出码 {proc.returncode}\n{text}", 8000) + tail


TOOLS = [
    Tool("run_shell", "在工作文件夹里执行一条 shell 命令（zsh），返回退出码和输出。能用文件工具解决的别用它。",
         obj({"command": {"type": "string", "description": "要执行的命令"},
              "timeout": {"type": "integer", "description": "超时秒数，默认 120"}}, ["command"]),
         run_shell, EXEC, lambda a: f"执行 {a.get('command', '')[:80]}", "执行"),
]
