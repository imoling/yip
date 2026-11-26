#!/usr/bin/env python3
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""连跑 N 遍完整流程（建任务 → 补充 → 追加 → 推翻 → 改判断 → 交付），任何一遍卡住或出错就退出 1。

    .venv/bin/python scripts/run10.py [N]
在进程内起整个应用，用临时数据目录，不碰 data/。
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp(dir=Path.home() / "Library" / "Caches"))
os.environ["SA_DATA_DIR"] = str(tmp)
os.environ.setdefault("SA_STEP_DELAY", "0.05,0.15")
for k in ("LLM_BASE_URL", "LLM_MODEL"):
    os.environ.pop(k, None)

import httpx  # noqa: E402

from server import db  # noqa: E402
from server.main import app  # noqa: E402

GOAL = "帮我整理一份三家竞品的简报，重点看定价和渠道。"
SAY = ["定价那块重点看企业版，别管个人版", "顺便再加一家竞品", "渠道不查了，换成查客户案例"]


async def wait(pred, timeout=60):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def one(c, i):
    mid = (await c.post("/api/tasks", json={"goal": GOAL})).json()["id"]
    assert await wait(lambda: len(db.children(mid)) >= 2, 5), "5 秒内没拆出子任务"
    decs = []
    for text in SAY:
        d = (await c.post("/api/interject", json={"text": text, "task_id": mid})).json()
        decs.append(d)
        if d.get("need_confirm"):
            await c.post(f"/api/decisions/{d['decision_id']}/confirm")
        await asyncio.sleep(0.2)
    await c.post(f"/api/decisions/{decs[0]['decision_id']}/override", json={"intent": "append"})
    assert await wait(lambda: db.task(mid)["status"] in db.TERMINAL, 60), "60 秒内没交付"
    t = db.task(mid)
    failed = [k["label"] for k in db.children(mid) if k["status"] == "failed"]
    assert t["status"] == "done" and Path(t["artifact"]).exists() and not failed, (t["status"], failed)
    return [d["intent"] for d in decs]


async def main(n):
    ok = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=30) as c:
            for i in range(n):
                t0 = time.monotonic()
                try:
                    intents = await one(c, i)
                    ok += 1
                    print(f"第 {i + 1} 遍 通过 {time.monotonic() - t0:.1f} s  {intents}")
                except AssertionError as e:
                    print(f"第 {i + 1} 遍 失败：{e}")
    print(f"\n{ok}/{n} 通过")
    return ok == n


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)) else 1)
