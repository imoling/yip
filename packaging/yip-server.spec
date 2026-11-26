# -*- mode: python ; coding: utf-8 -*-
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""PyInstaller spec：把后端打成 onedir 的 `iml-yip-server`，供 Tauri 壳作为子进程拉起。

参照 OpenWorker（MIT）的 packaging/openworker-server.spec。用 onedir 不用 onefile：
onefile 每次启动都要把整个包解到临时目录，慢好几秒；技能脚本每跑一次都会再起一次本程序，更不能慢。

产物：dist/iml-yip-server/{iml-yip-server, _internal/}，内置员工在 _internal/workers/。
构建：packaging/build_server.sh
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

PACKAGING = SPECPATH  # noqa: F821 — PyInstaller 注入
ROOT = os.path.dirname(PACKAGING)

hiddenimports = []
datas = [(os.path.join(ROOT, "workers"), "workers"), (os.path.join(ROOT, "skills"), "skills")]
binaries = []

# langgraph 是命名空间包，checkpoint / sqlite 存档器分在几个发行包里，按子模块全收
for pkg in ("server", "langgraph", "langchain_core", "langchain", "langchain_openai", "deepagents", "aiosqlite", "ormsgpack"):
    hiddenimports += collect_submodules(pkg)

# uvicorn 的协议、生命周期实现是按字符串动态加载的；certifi 的根证书要随包带上
# 连接器（mcp）和联网搜索（ddgs 及其依赖 primp / lxml）也有按字符串加载的部分，整包收
for pkg in ("uvicorn", "certifi", "anyio", "ormsgpack", "ddgs", "primp", "lxml", "httpx_sse", "sse_starlette",
            "pydantic_settings", "jsonschema", "jsonschema_specifications", "docx", "openpyxl", "pptx", "pypdf", "et_xmlfile",
            # Deep Agents（试验引擎）、浏览器（Playwright 带自己的 driver）、操作电脑（pyobjc）、图片验收（Pillow）
            "deepagents", "langchain_openai", "tiktoken", "playwright", "PIL", "Quartz", "ApplicationServices",
            "AppKit", "Foundation", "objc", "CoreFoundation", "HIServices"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# mcp.cli 依赖没装的 typer，一导入就退出；只收客户端要用的部分
hiddenimports += collect_submodules("mcp", filter=lambda n: not n.startswith(("mcp.cli", "mcp.server")))

# FastAPI 处理上传时才导入 python-multipart
hiddenimports += ["multipart", "python_multipart"]

# 技能脚本由本程序的 --run-script 执行，只能用打进来的标准库；先收齐脚本常用的几个
hiddenimports += [
    "argparse", "csv", "datetime", "decimal", "difflib", "fractions", "hashlib", "html", "math",
    "random", "statistics", "string", "textwrap", "unicodedata", "urllib.parse", "urllib.request",
    "xml.etree.ElementTree", "zipfile",
]

a = Analysis(
    [os.path.join(PACKAGING, "server_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="iml-yip-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 不开控制台会把 stdout/stderr 置空，uvicorn 起不来
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="iml-yip-server",
)
