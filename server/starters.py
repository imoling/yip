# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""一键场景包：降低第一句的门槛，既可以直接开跑，也能一键存成定时跟进。"""
from __future__ import annotations

STARTERS: list[dict] = [
    {
        "id": "api-pricing",
        "title": "模型定价对比",
        "tag": "调研",
        "prompt": "帮我查一下 DeepSeek 和 Kimi 的 API 公开定价，整理成一页对比",
        "mode": "once",
    },
    {
        "id": "competitor-brief",
        "title": "三家竞品简报",
        "tag": "调研",
        "prompt": "整理三家竞品的简报，重点看定价和渠道",
        "mode": "once",
    },
    {
        "id": "workspace-check",
        "title": "盘点工作文件夹",
        "tag": "办公",
        "prompt": "工作文件夹里有哪些文件？按类型简单梳理一下",
        "mode": "once",
    },
    {
        "id": "daily-ai-news",
        "title": "每日 AI 动态早报",
        "tag": "定时",
        "prompt": "搜集过去 24 小时值得关注的 AI 产品发布与模型更新，整理成一份简明早报",
        "mode": "schedule",
        "rule": {"type": "weekdays", "time": "08:30"},
        "when": "工作日 08:30",
    },
    {
        "id": "weekly-report",
        "title": "周五工作周报",
        "tag": "定时",
        "prompt": "汇总工作文件夹里本周的产出文件，起草一份给领导看的周报（先结论后细节）",
        "mode": "schedule",
        "rule": {"type": "weekly", "time": "17:00", "days": [5]},
        "when": "每周五 17:00",
    },
    {
        "id": "mail-digest",
        "title": "晨间邮件摘要",
        "tag": "定时",
        "prompt": "整理昨天到今早的新邮件，挑出需要我回复或拍板的事项并给处理建议",
        "mode": "schedule",
        "rule": {"type": "weekdays", "time": "08:45"},
        "when": "工作日 08:45",
    },
]


def listing() -> list[dict]:
    return [dict(s) for s in STARTERS]
