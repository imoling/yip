#!/usr/bin/env python3
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""查服务政策（演示用 Mock 数据，只依赖标准库）。

用法：python3 -I main.py --input "<任务说明>" [--constraints '<JSON 数组>'] [--round N]
"""
import argparse
import json
import re

ROWS = [
    [
        "竞品甲：企业版 7×24 工单，P1 故障 2 小时响应",
        [
            "SLA"
        ]
    ],
    [
        "竞品乙：私有化含 20 人天实施，超出 3000 元/人天",
        [
            "实施"
        ]
    ],
    [
        "竞品丙：标准 SLA 99.9%，未达标按月费 10% 赔付",
        [
            "SLA"
        ]
    ],
    [
        "竞品乙：每年 2 次免费培训，线上为主",
        [
            "培训"
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
