#!/usr/bin/env python3
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""跑 30 条测试集，打印准确率、分水岭对照、混淆矩阵和延迟。

    .venv/bin/python scripts/eval_classify.py            # 用「模型服务」里配的模型（超时照产品设定 3 s）
    .venv/bin/python scripts/eval_classify.py --timeout 10   # 放宽超时，只看语义判得准不准
    .venv/bin/python scripts/eval_classify.py --rules    # 只用本地规则
"""
import argparse
import asyncio
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import db, settings  # noqa: E402

real = settings.get()

from server.router import classify  # noqa: E402
from tests.conftest import load_cases, seed_context  # noqa: E402

# conftest 一导入就会清掉 LLM_*（测试要离线跑），所以模型配置要在它之后再放回去
for k, env in [("base_url", "LLM_BASE_URL"), ("api_key", "LLM_API_KEY"), ("model", "LLM_MODEL"), ("fast_model", "LLM_FAST_MODEL")]:
    os.environ[env] = real[k]


async def main(a):
    if a.rules:
        os.environ["LLM_BASE_URL"] = ""
    classify.timeout = lambda: a.timeout
    tmp = Path(tempfile.mkdtemp(dir=Path.home() / "Library" / "Caches"))
    db.DATA_DIR = tmp
    db.reset_for_tests(tmp / "app.db")
    ids = seed_context()
    label_of = {v: k for k, v in ids.items()}
    cases = load_cases()
    ok, conf, src, lat, groups = 0, Counter(), Counter(), [], {}
    for c in cases:
        t0 = time.monotonic()
        d = await classify.classify(c["text"], ids["main"])
        lat.append(time.monotonic() - t0)
        hit = d.intent == c["intent"]
        ok += hit
        conf[(c["intent"], d.intent)] += 1
        src[d.source] += 1
        if c["group"]:
            g = groups.setdefault(c["group"], [0, 0])
            g[0] += hit
            g[1] += 1
        mark = "✓" if hit else "✗"
        print(f"{mark} {c['text']:<24} 期望 {c['intent']:<8} 判为 {d.intent:<8} 目标 {label_of.get(d.target, d.target)} "
              f"{d.confidence:.2f} {d.source}  {d.reason}")
    n = len(cases)
    print(f"\n准确率 {ok}/{n} = {ok / n:.0%}（目标 ≥ 90%）")
    if not a.rules and src["model"] == 0:
        print("\n模型一次都没用上：先在「模型服务」里配好并测通")
    print("来源：", dict(src), f"  平均 {sum(lat) / n:.2f} s，最慢 {max(lat):.2f} s")
    print("分水岭对照：", {g: f"{v[0]}/{v[1]}" for g, v in groups.items()})
    print("混淆（期望 → 判为）：", {f"{k[0]}→{k[1]}": v for k, v in conf.items() if k[0] != k[1]})
    return ok / n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", action="store_true")
    ap.add_argument("--timeout", type=float, default=3.0)
    acc = asyncio.run(main(ap.parse_args()))
    sys.exit(0 if acc >= 0.9 else 1)
