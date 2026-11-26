"""队员能调的工具：统一成 OpenAI function calling 的形状，外加风险级别。

一个工具 = Tool(name, description, parameters, run, risk, summarize)。
run(ctx, **args) 是协程，返回给模型看的文本；出错抛 ToolError，文本作为结果回给模型，不中断循环。
风险级别和放行规则见 risk.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

READ, WRITE, EXEC, EXTERNAL = "read", "write", "exec", "external"


class ToolError(Exception):
    pass


@dataclass
class Ctx:
    task_id: str
    main_id: str
    worker_id: str
    workspace: Path
    constraints: list[str] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[..., Awaitable[str]]
    risk: str = READ
    summarize: Callable[[dict], str] | None = None   # 放行卡片和动态里显示的一句话
    label: str = ""                                   # 中文动作名：搜索、读网页、写入…

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

    def brief(self, args: dict) -> str:
        if self.summarize:
            try:
                return self.summarize(args)
            except Exception:  # noqa: BLE001 — 摘要坏了不能挡住执行
                pass
        return f"{self.label or self.name} {str(args)[:80]}"


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props)}


def clip(text: str, n: int = 8000) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + f"\n…（还有 {len(text) - n} 字没显示）"
