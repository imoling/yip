#!/usr/bin/env python3
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""收集渠道政策（演示用 Mock 数据，只依赖标准库）。

用法：python3 -I main.py --input "<任务说明>" [--constraints '<JSON 数组>'] [--round N]
"""
import argparse
import json
import re

ROWS = [
    [
        "竞品甲：三级代理，金牌返点 25%，区域独家需年签 300 万",
        [
            "代理"
        ]
    ],
    [
        "竞品乙：只走总代 + ISV 生态，ISV 分成 30%",
        [
            "生态"
        ]
    ],
    [
        "竞品丙：直销为主，央国企项目走集成商，返点 10%–15%",
        [
            "直销"
        ]
    ],
    [
        "竞品甲：经销商名单 42 家，华东 17 家",
        [
            "经销商"
        ]
    ]
]


def hit(key, text, tags):
    return key in text or any(t in key or key in t for t in tags)


def keep(row, constraints):
    """「只看 X」只留带 X 的行（没标签的行不受影响）；「别管 X」去掉带 X 的行。"""
    text, tags = row
    for c in constraints:
        for m in re.finditer(r"(?:只看|只要|重点看|聚焦)([^，。,；;\s]+)", c):
            if tags and not hit(m.group(1), text, tags):
                return False
        for m in re.finditer(r"(?:别管|不要|不看|去掉|排除)([^，。,；;\s]+)", c):
            if hit(m.group(1), text, tags):
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="")
    ap.add_argument("--constraints", default="[]")
    ap.add_argument("--round", type=int, default=1)
    a = ap.parse_args()
    try:
        cons = json.loads(a.constraints)
    except json.JSONDecodeError:
        cons = []
    rows = [r for r in ROWS if keep(r, cons)]
    if not rows:
        print("未查到符合约束的结果")
        return
    n = max(1, len(rows))
    start = (a.round - 1) % n
    picked = (rows[start:] + rows[:start])[:3]
    for text, _ in picked:
        print("- " + text)
    if cons:
        print("（已按约束：" + "；".join(cons) + "）")


if __name__ == "__main__":
    main()
