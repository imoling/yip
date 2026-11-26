"""打开网页检查：队员（尤其测试）用无头浏览器打开工作文件夹里的网页，看报错、页面文字，还能跑一段 JS 模拟操作。"""
from __future__ import annotations

import json

from .. import verify
from . import READ, Ctx, Tool, ToolError, obj
from .files import inside


async def check_page(ctx: Ctx, path: str, script: str = "", wait_ms: int = 3000) -> str:
    p = inside(ctx, path)
    if not p.is_file() or p.suffix.lower() not in (".html", ".htm"):
        raise ToolError(f"没有这个网页：{path}")
    if not verify.browser():
        raise ToolError("这台电脑上没有 Chrome / Edge，没法打开网页；改用读代码检查")
    r = await verify.run_page(p, script, max(500, min(int(wait_ms or 3000), 15000)))
    out = [f"打开 {p.name}：{'成功' if r['ok'] else '浏览器异常退出'}",
           "报错：" + ("；".join(r["errors"]) if r["errors"] else "没有")]
    if script.strip():
        out.append("脚本结果：" + (json.dumps(r["result"], ensure_ascii=False)[:2000] if r["result"] is not None else "没有返回值"))
    if r["logs"]:
        out.append("console：" + "；".join(r["logs"][:10]))
    out.append("页面文字：" + (r["text"][:1500] or "（空）") + ("（页面里有 canvas）" if r["canvas"] else ""))
    return "\n".join(out)


TOOLS = [
    Tool("check_page",
         "用无头浏览器打开工作文件夹里的网页，返回报错、console 输出和页面文字。"
         "script 可选：一段 JS，页面加载后执行，可以 await、可以 return 一个值，用来模拟按键、点按钮、读游戏状态，"
         "比如 document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp'})); await new Promise(r => setTimeout(r, 500)); "
         "return document.querySelector('#score').textContent。时间是虚拟的，setTimeout 会快进。",
         obj({"path": {"type": "string"}, "script": {"type": "string"},
              "wait_ms": {"type": "integer", "description": "页面上跑多久（毫秒，默认 3000）"}}, ["path"]),
         check_page, READ, lambda a: f"打开网页检查 {a.get('path')}", "打开网页"),
]
