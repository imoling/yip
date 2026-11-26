#!/usr/bin/env python3
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""生成内置队员包到 workers/。改了这里的数据后重跑：

    .venv/bin/python scripts/build_builtin_workers.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "workers"
sys.path.insert(0, str(ROOT))

COMPETITORS = ["竞品甲", "竞品乙", "竞品丙"]

# 每个技能的 Mock 数据：rows 是候选结果，每行可带 tags，约束里出现「只看 X / 重点 X」就只留带 X 的行，「别管 X / 不要 X」就去掉带 X 的行
WORKERS = [
    {
        "dir": "情报调研员", "id": "intel-researcher", "name": "情报调研员", "team": "调研小队",
        "introduction": "盯竞品和政策：查报价、渠道和服务政策，每条都带出处。只摆事实，不替你下结论。",
        "personality": ["条条带出处", "只摆事实", "查不到就说查不到"],
        "inputs": "要查的对象（哪几家、哪个产品线）和关注点（定价、渠道、服务、动态）",
        "deliverable": "一张按对象分组的事实清单，每条带来源和日期；查不到的项写明「未查到」",
        "caps": [{
            "name": "竞品情报", "description": "查竞品报价、渠道与代理政策、服务政策和近期动态",
            "skills": [
                ("search-competitor-price", "检索竞品报价", "按竞品和版本检索公开报价、折扣和计费方式", "定价 报价 价格 收费 版本 企业版 个人版", [
                    ("竞品甲 企业版：按席位 98 元/人/月，50 席起，年付 8 折", ["企业版"]),
                    ("竞品甲 个人版：免费版 + 专业版 19 元/月", ["个人版"]),
                    ("竞品乙 企业版：私有化一次性 60 万起，年服务费 15%", ["企业版"]),
                    ("竞品乙 个人版：39 元/月，含 100 次高级调用", ["个人版"]),
                    ("竞品丙 企业版：按调用量计费，0.02 元/千 token，月保底 2 万", ["企业版"]),
                    ("竞品丙 个人版：暂未开放个人版", ["个人版"]),
                ]),
                ("collect-channel-policy", "收集渠道政策", "收集代理商分级、返点、区域保护和招募条件", "渠道 代理 经销 分销 返点 伙伴", [
                    ("竞品甲：三级代理，金牌返点 25%，区域独家需年签 300 万", ["代理"]),
                    ("竞品乙：只走总代 + ISV 生态，ISV 分成 30%", ["生态"]),
                    ("竞品丙：直销为主，央国企项目走集成商，返点 10%–15%", ["直销"]),
                    ("竞品甲：经销商名单 42 家，华东 17 家", ["经销商"]),
                ]),
                ("lookup-service-policy", "查服务政策", "查售后、SLA、实施交付和培训政策", "服务 售后 SLA 实施 交付 培训 支持", [
                    ("竞品甲：企业版 7×24 工单，P1 故障 2 小时响应", ["SLA"]),
                    ("竞品乙：私有化含 20 人天实施，超出 3000 元/人天", ["实施"]),
                    ("竞品丙：标准 SLA 99.9%，未达标按月费 10% 赔付", ["SLA"]),
                    ("竞品乙：每年 2 次免费培训，线上为主", ["培训"]),
                ]),
                ("track-competitor-news", "跟踪竞品动态", "跟踪发布会、融资、大客户签约等公开动态", "动态 新闻 发布 融资 签约 客户", [
                    ("竞品甲 10 月发布 3.0，主打多智能体协作", ["发布"]),
                    ("竞品乙 拿下某省政务云项目，金额未公开", ["签约"]),
                    ("竞品丙 完成 C 轮融资", ["融资"]),
                ]),
            ],
        }],
        "prompts": [("查报价", "说清看哪几家、哪个版本", "查一下三家竞品企业版的报价和计费方式")],
    },
    {
        "dir": "数据分析员", "id": "data-analyst", "name": "数据分析员", "team": "调研小队",
        "introduction": "把多份资料放在一起比：功能清单、价格、指标，出对照表，标出差异和异常。数字只引原始数据。",
        "personality": ["数字不改写", "差异标清楚", "口径先对齐"],
        "inputs": "要比的对象和维度；有现成数据就给出处",
        "deliverable": "一张对照表加三条以内的差异要点，表里每个数字都能追到来源",
        "caps": [{
            "name": "比对分析", "description": "多源比对功能、价格和指标，出对照表并标出差异",
            "skills": [
                ("compare-features", "比对功能清单", "逐项比对各家产品的功能有无和强弱", "功能 能力 差异 清单 特性 对比", [
                    ("多智能体编排：甲 有 / 乙 有 / 丙 无", ["编排"]),
                    ("私有化部署：甲 有 / 乙 有 / 丙 仅专属云", ["私有化"]),
                    ("中途干预：甲 仅取消 / 乙 无 / 丙 无", ["干预"]),
                    ("知识库：三家都有，乙支持本体", ["知识库"]),
                ]),
                ("build-comparison-table", "出对照表", "把多源数据按统一口径整理成对照表", "对照表 表格 汇总 口径 维度", [
                    ("| 维度 | 竞品甲 | 竞品乙 | 竞品丙 |", []),
                    ("| 计费 | 按席位 | 私有化买断 | 按调用量 |", ["计费"]),
                    ("| 渠道 | 三级代理 | 总代 + ISV | 直销 + 集成商 |", ["渠道"]),
                ]),
                ("check-anomaly", "异常溯源", "找出数据里前后不一致或明显异常的项并追来源", "异常 核对 不一致 溯源 校验 数据", [
                    ("竞品乙 两个来源报价差 20%，官网为准", ["报价"]),
                    ("竞品丙 SLA 口径为月度，其他两家为年度", ["SLA"]),
                ]),
            ],
        }],
        "prompts": [("比功能", "说清比哪几家、哪些维度", "比一下三家竞品的功能差异，出对照表")],
    },
    {
        "dir": "报告撰写员", "id": "report-writer", "name": "报告撰写员", "team": "调研小队",
        "introduction": "把调研和分析结果写成能直接交出去的简报或方案：先结论后论据，Markdown 交付。",
        "personality": ["先结论后论据", "不写营销词", "缺的事实留占位"],
        "inputs": "写给谁、写什么、篇幅；已有的调研结论和数据",
        "deliverable": "一份 Markdown 文档：结论在前，每条论据带来源，缺的事实用【xx】占位",
        "caps": [{
            "name": "文档撰写", "description": "拟提纲、起草章节、整理成可交付的 Markdown 简报或方案",
            "skills": [
                ("outline-report", "拟提纲", "按读者和目的拟出文档提纲", "提纲 大纲 结构 目录 方案 简报 报告", [
                    ("一、结论（三句话）", []), ("二、定价对比", ["定价"]), ("三、渠道对比", ["渠道"]),
                    ("四、服务与交付", ["服务"]), ("五、建议与待确认", []),
                ]),
                ("draft-section", "起草章节", "按提纲逐节起草正文，论据带来源", "起草 撰写 写作 正文 章节 文案", [
                    ("结论：三家都在推企业版，差异在计费方式和交付深度", []),
                    ("定价：甲按席位、乙买断、丙按量，适合的客户规模不同", ["定价"]),
                    ("渠道：甲靠代理铺量，乙靠生态，丙靠直销和集成商", ["渠道"]),
                ]),
                ("polish-markdown", "整理成文档", "统一格式、补占位、生成最终 Markdown", "整理 排版 格式 Markdown 交付 文档", [
                    ("已统一标题层级和表格格式", []), ("缺失数据已用【xx】占位", []),
                ]),
            ],
        }],
        "prompts": [("写简报", "说清写给谁、看什么", "把调研结果写成一页竞品简报，写给分管领导")],
    },
    {
        "dir": "通用助理", "id": "general-assistant", "name": "通用助理", "team": "办公小队",
        "introduction": "没有合适的专职队员时由我接手：查资料、记要点、整理清单。专业判断我会标出来请专人确认。",
        "personality": ["什么都接", "不懂就标出来", "交付清单化"],
        "inputs": "要做的事和期望的交付形式",
        "deliverable": "一份要点清单，专业判断处标「待专人确认」",
        "caps": [{
            "name": "通用事务", "description": "查资料、记要点、整理清单，兜底处理没有专职队员的事",
            "skills": [
                ("general-research", "查资料", "围绕任务查公开资料并摘要", "资料 查询 搜索 了解 背景", [
                    ("已找到 3 份相关公开资料", []), ("资料 1：行业报告摘要【xx】", []), ("资料 2：公开新闻【xx】", []),
                ]),
                ("take-notes", "整理要点", "把零散信息整理成要点清单", "要点 整理 清单 记录 纪要", [
                    ("要点已整理为 3 条", []), ("涉及专业判断处已标「待专人确认」", []),
                ]),
            ],
        }],
        "prompts": [("随便问", "说清要做什么", "帮我整理一下这件事的背景资料")],
    },
    {
        "dir": "需求分析师", "id": "requirements-analyst", "name": "需求分析师", "team": "软件开发小队",
        "introduction": "把一句话需求问清楚、拆成功能清单，写出能验收的标准。不写代码，不替你拍板优先级。",
        "personality": ["先问清再动手", "每条都能验收", "不替人拍板"],
        "inputs": "要做什么、给谁用、有没有现成的代码或文档",
        "deliverable": "一份需求说明：背景、功能清单（每条带验收标准）、不做什么、待确认的问题",
        "caps": [{
            "name": "需求分析", "description": "澄清需求、拆功能、写验收标准",
            "skills": [
                ("clarify-requirements", "澄清需求", "找出需求里模糊、矛盾、缺失的地方，列成待确认问题", "需求 澄清 问题 模糊 确认", [
                    ("待确认：登录要不要支持手机号", []), ("待确认：导出格式是 Excel 还是 CSV", []),
                ]),
                ("write-user-stories", "拆功能清单", "把需求拆成一条条用户故事，每条带验收标准", "功能 清单 用户故事 拆分 PRD", [
                    ("作为管理员，我能批量导入员工，验收：上传 100 行的表 10 秒内完成", []),
                    ("作为员工，我能查看自己的工单，验收：只看到自己的", []),
                ]),
            ],
        }],
        "prompts": [("拆需求", "说清做什么、给谁用", "我想做一个内部报销小工具，帮我拆一下需求")],
    },
    {
        "dir": "开发工程师", "id": "developer", "name": "开发工程师", "team": "软件开发小队",
        "introduction": "在工作文件夹里读代码、写代码、跑命令、修 bug。改之前先读懂，改完自己跑一遍。",
        "personality": ["先读懂再改", "改完自己跑", "改动说清楚"],
        "inputs": "代码在哪（工作文件夹里的路径或 Git 仓库）、要做什么、怎么算做完",
        "deliverable": "能跑的代码改动，加一段说明：改了哪些文件、为什么、怎么验证",
        "caps": [{
            "name": "编码", "description": "读代码、写代码、跑命令、修 bug",
            "skills": [
                ("implement-feature", "写功能", "按需求在现有代码里加功能，带最小的测试", "开发 实现 功能 代码 编写", [
                    ("已新增 export.py，支持导出 CSV", []), ("已补 tests/test_export.py，3 个用例通过", []),
                ]),
                ("fix-bug", "修 bug", "复现问题、定位原因、改掉并验证", "修复 bug 报错 问题 异常", [
                    ("原因：日期解析没处理时区", []), ("已修复并补测试，pytest 全过", []),
                ]),
                ("run-and-check", "跑起来看", "装依赖、跑程序或测试，把输出整理给你", "运行 跑 安装 依赖 启动", [
                    ("npm install 完成，npm test 12 个通过", []),
                ]),
            ],
        }],
        "prompts": [("写个脚本", "说清输入输出", "在工作文件夹里写一个脚本，把 data.csv 按月份汇总成 summary.csv")],
    },
    {
        "dir": "测试工程师", "id": "tester", "name": "测试工程师", "team": "软件开发小队",
        "introduction": "写测试、跑测试、复现问题，出一份能照着复现的测试报告。只报问题，不改业务代码。",
        "personality": ["能复现才算数", "边界都试", "只报不改"],
        "inputs": "要测的代码或功能、预期行为",
        "deliverable": "测试报告：测了什么、通过几条、失败的每条带复现步骤和实际结果",
        "caps": [{
            "name": "测试", "description": "设计用例、写测试、跑测试、出报告",
            "skills": [
                ("design-test-cases", "设计用例", "按需求列正常、边界、异常三类用例", "用例 测试点 边界 场景", [
                    ("正常：导入 10 行", []), ("边界：导入 0 行、10 万行", []), ("异常：表头缺列", []),
                ]),
                ("run-tests", "跑测试", "跑现有测试或新写的测试，整理结果", "测试 pytest 单元测试 回归 跑测试", [
                    ("pytest：45 通过，2 失败", []), ("失败：test_import_empty 期望提示，实际报 500", []),
                ]),
            ],
        }],
        "prompts": [("测一下", "说清测什么", "给工作文件夹里的 calc.py 写测试并跑一遍")],
    },
    {
        "dir": "代码审查员", "id": "code-reviewer", "name": "代码审查员", "team": "软件开发小队",
        "introduction": "读代码挑问题：正确性、安全、可维护性，按严重程度排好，每条给改法。只读不改。",
        "personality": ["先挑要命的", "每条给改法", "只读不改"],
        "inputs": "要审的代码（路径或 Git 改动）、关注点",
        "deliverable": "审查意见：按严重 / 一般 / 建议分级，每条带位置（文件:行）、问题、改法",
        "caps": [{
            "name": "代码审查", "description": "找 bug、安全隐患和坏味道，给改进建议",
            "skills": [
                ("review-code", "审代码", "逐文件读，找正确性和可维护性问题", "审查 review 代码 质量 问题", [
                    ("严重：utils.py:42 除零未处理", []), ("一般：重复的日期解析逻辑可以抽函数", []),
                ]),
                ("security-check", "查安全隐患", "找注入、密钥泄露、越权等安全问题", "安全 漏洞 注入 密钥 权限", [
                    ("严重：config.py 里明文写了数据库密码", []),
                ]),
            ],
        }],
        "prompts": [("审一下", "说清审哪些文件", "审一下工作文件夹里 app/ 目录的代码")],
    },
    {
        "dir": "邮件助理", "id": "mail-assistant", "name": "邮件助理", "team": "办公小队",
        "introduction": "整理收件箱、写邮件摘要、起草回复。发信每封都先问你，不替你做决定。",
        "personality": ["先摘要后细节", "回复简短得体", "发信必先问"],
        "inputs": "要处理哪段时间、哪类邮件；回复时说清你的态度",
        "deliverable": "邮件摘要（谁、什么事、要不要你处理、截止时间）或回复草稿",
        "caps": [{
            "name": "邮件处理", "description": "搜信、读信、写摘要、起草和发送回复",
            "skills": [
                ("triage-inbox", "整理收件箱", "把一段时间的邮件按要处理、知悉、可忽略分好，写成摘要", "邮件 收件箱 整理 摘要 未读", [
                    ("要处理：李四 · 合同评审会议 · 周四 15:00", []), ("知悉：张三 · 第 47 周周报", []),
                ]),
                ("draft-reply", "起草回复", "按你的意思起草回复，存成草稿；你说发才发", "回复 起草 写邮件 回信", [
                    ("已起草：回复李四，确认周四参会", []),
                ]),
            ],
        }],
        "prompts": [("整理邮件", "说清看哪段时间", "整理一下最近 3 天的未读邮件，告诉我哪些要我处理")],
    }
]

SCRIPT = '''#!/usr/bin/env python3
# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""{name}（演示用 Mock 数据，只依赖标准库）。

用法：python3 -I main.py --input "<任务说明>" [--constraints '<JSON 数组>'] [--round N]
"""
import argparse
import json
import re

ROWS = {rows}


def hit(key, text, tags):
    return key in text or any(t in key or key in t for t in tags)


def keep(row, constraints):
    """「只看 X」只留带 X 的行（没标签的行不受影响）；「别管 X」去掉带 X 的行。"""
    text, tags = row
    for c in constraints:
        for m in re.finditer(r"(?:只看|只要|重点看|聚焦)([^，。,；;\\s]+)", c):
            if tags and not hit(m.group(1), text, tags):
                return False
        for m in re.finditer(r"(?:别管|不要|不看|去掉|排除)([^，。,；;\\s]+)", c):
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
'''

AGENT_MD = """---
name: {id}
version: "1.0.0"
description: "{name}：{introduction}"
---

# {name}

## 身份

你是{name}。{introduction}

## 工作方式

1. 收到任务后，先在能力路由表里找对口的技能，用 load_skill 读它的做法再动手。
2. 每轮开始前重读要求；插话补充的要求和原任务冲突时，以后来的为准。
3. 只写查到的事实，不编数字；查不到、打不开就如实说明。
4. 路由表未命中时，用通用的搜索、读网页、读写文件把事做完，并说明哪些超出了专长。

## 能力路由

{routes}

## 需要的输入

{inputs}

## 交付标准

{deliverable}

## 安全规则

- 只在工作文件夹里读写，不删别人的文件，不发消息、不对外提交任何东西。

## 信息保护

- 不在产出里写个人联系方式、密钥和内部账号。
"""

SKILL_MD = """---
name: {id}
version: 1.0.0
description: |
  {desc}
  适用：{when}。
  不适用：{not_when}。
---

# {name}

## 怎么做

{how}

## 交付

- 用 write_file 把结果写成 Markdown，放在这件事的文件夹里，文件名用中文说清是什么
- 每条事实带出处（网址或文件名）和日期；查不到的写【待补充】，不编数字

## 离线演示

没配模型时系统自动运行 scripts/main.py 返回演示数据；配了模型时不要运行它。
"""

HOW = {
    "search-competitor-price": "1. web_search 搜各家「官网 定价 / pricing / 价格说明」，优先官网和官方文档\n2. web_fetch 打开定价页原文，按版本（个人 / 企业 / 私有化）和计费方式记下价格、单位、币种、生效日期\n3. 各家口径不一致时原样记下，别自己换算，除非要求里写了要换算",
    "collect-channel-policy": "1. web_search 搜「伙伴计划 / 代理政策 / 渠道招募 / ISV 合作」\n2. web_fetch 看原文，整理代理分级、返点比例、区域保护、准入门槛\n3. 只写公开能查到的，内部政策写【待补充】",
    "lookup-service-policy": "1. web_search 搜「服务协议 / SLA / 售后 / 实施交付 / 培训」\n2. web_fetch 看条款原文，记下响应时限、可用性承诺、赔付规则、实施人天和培训次数",
    "track-competitor-news": "1. web_search 搜近 6 个月的发布会、融资、大客户签约新闻\n2. web_fetch 核对 2 到 3 条最重要的，按时间倒序列出，每条一句话加出处",
    "compare-features": "1. 先 list_dir / read_file 看工作文件夹里已有的资料\n2. 不够再 web_search + web_fetch 查产品文档\n3. 逐项列功能矩阵：有 / 无 / 部分支持，备注写依据",
    "build-comparison-table": "1. 先 read_file 收齐已有数据\n2. 统一口径（币种、单位、周期），口径对不齐的单独标出\n3. 写成 Markdown 表格，表下三条以内的差异要点",
    "check-anomaly": "1. read_file 读齐各来源\n2. 同一指标多个来源交叉比对，列出不一致项、差多少、以哪个为准、为什么",
    "outline-report": "1. 弄清写给谁、看什么\n2. 拟提纲：结论（三句话）→ 论据 → 建议 → 待确认事项",
    "draft-section": "1. read_file 读调研资料\n2. 按提纲逐节写，先结论后论据，论据带出处，缺的写【xx】",
    "polish-markdown": "1. 统一标题层级和表格格式\n2. 补齐占位，写成最终文件",
    "general-research": "1. web_search 搜公开资料，web_fetch 读 2 到 4 篇原文\n2. 摘要成要点，每条带出处",
    "take-notes": "1. 把零散信息归并去重\n2. 整理成要点清单，专业判断处标「待专人确认」",
    "clarify-requirements": "1. 先读主人给的材料和工作文件夹里已有的文档、代码\n2. 按「谁用、做什么、做到什么程度、不做什么」逐项找模糊和矛盾\n3. 列成待确认问题，每条给你建议的默认答案",
    "write-user-stories": "1. 按角色拆成用户故事：作为…，我能…，以便…\n2. 每条写可验收的标准（数字、页面、边界）\n3. 标出优先级建议和依赖关系",
    "implement-feature": "1. list_dir / read_file 读懂现有结构和约定\n2. 小步改，write_file / edit_file 落盘\n3. run_shell 跑起来或跑测试，确认能用\n4. 写清改了哪些文件、怎么验证",
    "fix-bug": "1. 先 run_shell 复现，记下报错原文\n2. 读相关代码定位根因，别只改表面\n3. 修好后补一个能复现原问题的测试，跑通",
    "run-and-check": "1. 读 README 或项目配置，找对的启动和测试命令\n2. run_shell 执行，输出太长只留关键部分\n3. 失败时说清卡在哪一步",
    "design-test-cases": "1. 读需求和代码，列出功能点\n2. 每个功能点写正常、边界、异常三类用例\n3. 用表格交付：用例、步骤、预期结果",
    "run-tests": "1. 找到现有测试命令（pytest、npm test…）先跑一遍\n2. 缺测试就写最小的测试文件再跑\n3. 失败的每条写复现步骤、预期和实际；不改业务代码",
    "review-code": "1. list_dir 看结构，read_file 逐个读要审的文件\n2. 先找正确性问题，再看可维护性\n3. 按严重 / 一般 / 建议分级，每条写「文件:行、问题、改法」；只读不改",
    "security-check": "1. 搜明文密钥、拼接 SQL、未校验的输入、越权访问\n2. 每条说清怎么被利用、怎么修",
    "triage-inbox": "1. mail_search 按时间捞信（默认最近 3 天、只看未读）\n2. mail_read 读要紧的几封\n3. 分成「要你处理 / 知悉 / 可忽略」写摘要：谁、什么事、截止时间",
    "draft-reply": "1. mail_read 读原信，记下 Message-ID\n2. 按主人的意思用 mail_draft 起草，简短得体\n3. 主人明确说「发」时才用 mail_send，发前会先请主人确认",
}


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    for w in WORKERS:
        root = OUT / w["dir"]
        adir = root / w["id"]
        (adir / "agents").mkdir(parents=True)
        caps, routes = [], []
        all_skills = [s for c in w["caps"] for s in c["skills"]]
        for c in w["caps"]:
            caps.append({"name": c["name"], "description": c["description"],
                         "skills": [{"name": s[1], "id": s[0], "description": s[2]} for s in c["skills"]]})
            routes.append(f"### {c['name']}\n\n| 用户说 | 技能 |\n|---|---|")
            for sid, sname, sdesc, kw, rows in c["skills"]:
                routes.append(f"| {kw.replace(' ', '、')} | `{sid}` {sname} |")
        for sid, sname, sdesc, kw, rows in all_skills:
            sdir = adir / "skills" / sid
            (sdir / "scripts").mkdir(parents=True)
            others = [o[1] for o in all_skills if o[0] != sid][:2]
            (sdir / "SKILL.md").write_text(SKILL_MD.format(
                id=sid, name=sname, desc=sdesc, when="、".join(kw.split()[:4]) + " 相关的任务",
                not_when="、".join(others) + "（用对应技能）" if others else "超出本队员能力的事",
                how=HOW[sid]), "utf-8")
            (sdir / "scripts" / "main.py").write_text(
                SCRIPT.replace("{name}", sname).replace("{rows}", json.dumps(rows, ensure_ascii=False, indent=4)), "utf-8")
        meta = {
            "name": w["name"], "type": "cc", "version": "1.0.0", "introduction": w["introduction"],
            "personality": w["personality"], "capabilities": caps,
            "prompts": [{"title": t, "subtitle": st, "content": ct} for t, st, ct in w["prompts"]],
        }
        (root / "yip.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        (adir / "agents" / f"{w['id']}.md").write_text(AGENT_MD.format(
            id=w["id"], name=w["name"], introduction=w["introduction"], routes="\n".join(routes),
            inputs=w["inputs"], deliverable=w["deliverable"]), "utf-8")
    # 小队单独记在 workers/_teams.json，不写进 yip.json
    (OUT / "_teams.json").write_text(json.dumps({w["id"]: w.get("team", "") for w in WORKERS}, ensure_ascii=False, indent=2), "utf-8")
    from server.workers.validate import validate_dir
    bad = 0
    for d in sorted(x for x in OUT.iterdir() if x.is_dir()):
        rep = validate_dir(d)
        print(("通过 " if rep.ok else "未通过 ") + d.name, rep.errors, rep.warns)
        bad += not rep.ok
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    build()
