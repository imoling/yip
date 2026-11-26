# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""向主人提问并给选项卡片：复用 gate / approvals 机制，点一下就接着跑。"""
from __future__ import annotations

from . import EXTERNAL, Ctx, Tool, obj


async def ask_user(ctx: Ctx, question: str, options: list[str] | None = None, multi: bool = False) -> str:
    """真正拿答案在 worker.tools_node 里从 approvals.answer 取；这里仅作兜底。"""
    return "主人暂未选择，按最稳妥的方案继续。"


def _brief(args: dict) -> str:
    q = str(args.get("question") or "").strip()
    opts = args.get("options") or []
    if isinstance(opts, list) and opts:
        return f"{q}（{' / '.join(str(x) for x in opts[:4])}）"[:120]
    return q[:120] or "请你确认一下方向"


TOOLS = [
    Tool(
        "ask_user",
        "遇到关键方向拿不准、有 2 到 5 个方案要主人拍板时，弹出选项卡片让主人点一下再继续。别为小事频繁问。",
        obj(
            {
                "question": {"type": "string", "description": "简短明确的问题，一句话说完"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2 到 5 个具体选项，每项不超过 24 字",
                },
                "multi": {"type": "boolean", "description": "是否允许多选，默认 false"},
            },
            ["question", "options"],
        ),
        ask_user,
        EXTERNAL,
        _brief,
        "问主人",
    )
]
