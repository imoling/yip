"""执行队员的技能：有脚本就起子进程跑，没脚本就让模型按 SKILL.md 模拟。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from .. import llm
from .package import Skill, WorkerCard

TIMEOUT = 30
MAX_OUT = 2000


def script_cmd(script: Path, args: list[str]) -> list[str]:
    """冻结后 sys.executable 是服务本身，由 server/run.py 的 --run-script 代为执行；源码运行用 python -I。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-script", str(script), *args]
    return [sys.executable, "-I", str(script), *args]


async def _run_script(skill: Skill, args: list[str]) -> str:
    script = Path(skill.dir) / "scripts" / "main.py"
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "HOME", "SANDBOX_BASE_URL", "TMPDIR")}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = await asyncio.create_subprocess_exec(
        *script_cmd(script, args), cwd=skill.dir, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return f"执行失败：{skill.name} 超过 {TIMEOUT} 秒没有返回"
    except asyncio.CancelledError:
        proc.kill()
        raise
    text = out.decode("utf-8", "replace").strip()
    if proc.returncode and not text.startswith("执行失败"):
        text = f"执行失败：{skill.name} 退出码 {proc.returncode}\n{text}"
    return text[:MAX_OUT]


def simple_cli(skill: Skill) -> bool:
    return "--input" in skill.doc or "离线演示" in skill.doc


async def run(card: WorkerCard, skill: Skill, task_input: str, constraints: list[str], round_no: int) -> str:
    if skill.has_script and simple_cli(skill):
        return await _run_script(skill, ["--input", task_input, "--constraints",
                                         json.dumps(constraints, ensure_ascii=False), "--round", str(round_no)])
    if not llm.configured():
        return f"（未配置模型，{skill.name} 只能按说明手动调用）"
    if skill.has_script:
        try:
            r = await llm.chat_json([
                {"role": "system", "content": "根据技能说明，给出调用脚本的命令行参数。只输出 JSON：{\"args\": [\"--flag\", \"值\", ...]}，不要包含 python 和脚本路径。"},
                {"role": "user", "content": f"技能说明：\n{skill.doc}\n\n任务：{task_input}\n约束：{constraints}"},
            ], fast=True, timeout=15)
            args = [str(a) for a in r.get("args", [])][:20]
        except Exception as e:
            return f"执行失败：没法从说明里得出参数（{e}）"
        return await _run_script(skill, args)
    try:
        return (await llm.chat([
            {"role": "system", "content": f"你在扮演{card.name}执行技能「{skill.name}」，按技能说明给出简短结果（5 行以内），不知道的写【xx】，不编数字。"},
            {"role": "user", "content": f"技能说明：\n{skill.doc}\n\n任务：{task_input}\n约束：{constraints}"},
        ], json_mode=False, timeout=30))[:MAX_OUT]
    except Exception as e:
        return f"执行失败：{e}"
