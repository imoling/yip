"""子进程入口：桌面壳拉起的后端服务；冻结后兼作技能脚本的解释器。

    python -m server.run [--port 8770] [--host 127.0.0.1]
    python -m server.run --run-script <脚本> 参数…

环境变量（都由 Tauri 壳注入，单独跑时可不设）：
    SA_API_TOKEN   设了就校验所有 /api/* 请求的令牌
    SA_DATA_DIR    数据目录；冻结后没设时用 ~/Library/Application Support/cn.imoling.yip
    SA_PARENT_PID  壳的 PID；它没了本进程也退出

父进程监视的做法参照 OpenWorker（MIT）的 coworker/server/run.py。
模块顶层只导入标准库：--run-script 每跑一次技能都会走这里，不能拖慢。
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

DEFAULT_PORT = 8770
APP_ID = "cn.imoling.yip"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def run_script(argv: list[str]) -> int:
    """照 `python -I 脚本 参数…` 的样子跑技能脚本：脚本目录不进 sys.path，退出码原样返回。"""
    if not argv:
        print("执行失败：--run-script 后面要跟脚本路径", file=sys.stderr)
        return 2
    import runpy
    import traceback

    for stream in (sys.stdout, sys.stderr):  # 冻结后不认 PYTHONIOENCODING，输出统一 UTF-8
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    script = os.path.abspath(argv[0])
    sys.argv = [script, *argv[1:]]
    # 同 -I：当前目录和脚本目录都不在 sys.path 里，免得同目录的 json.py 之类顶替标准库
    unsafe = {"", os.getcwd(), os.path.dirname(script)}
    sys.path[:] = [x for x in sys.path if (os.path.abspath(x) if x else "") not in unsafe]
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    except BaseException:  # noqa: BLE001 — 和解释器一样打印堆栈、退出码 1
        traceback.print_exc()
        return 1
    return 0


def _data_dir() -> Path | None:
    raw = os.environ.get("SA_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    if frozen():
        return Path.home() / "Library" / "Application Support" / APP_ID
    return None  # 源码运行：沿用 db.py 的 <仓库>/data


def _watch_parent() -> None:
    """SA_PARENT_PID 指向的进程没了就退出：先 SIGTERM 让 uvicorn 收尾，5 秒还没退就强退。

    盯显式 PID 而不是 getppid：壳被强杀或开发时热重启都会跳过壳自己的清理。
    """
    try:
        parent = int(os.environ.get("SA_PARENT_PID") or 0)
    except ValueError:
        parent = 0
    if not parent or sys.platform == "win32":  # Windows 上 os.kill(pid, 0) 会结束目标进程
        return
    original_ppid = os.getppid()

    def gone() -> bool:
        try:
            os.kill(parent, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return os.getppid() != original_ppid

    def watch() -> None:
        while not gone():
            time.sleep(1.5)
        print("父进程已退出，服务跟着退出", file=sys.stderr, flush=True)
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
        os._exit(0)

    threading.Thread(target=watch, name="sa-parent-watch", daemon=True).start()


def serve(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="iml-yip-server")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args(argv)

    d = _data_dir()
    if d is not None:
        d.mkdir(parents=True, exist_ok=True)
        os.environ["SA_DATA_DIR"] = str(d)  # 必须在导入 server.db 之前

    import uvicorn

    from .main import app

    _watch_parent()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", lifespan="on")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--run-script"]:
        sys.exit(run_script(argv[1:]))
    serve(argv)


if __name__ == "__main__":
    main()
