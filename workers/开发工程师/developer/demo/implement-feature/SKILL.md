---
name: implement-feature
version: 1.0.0
description: |
  按需求在现有代码里加功能，带最小的测试
  适用：开发、实现、功能、代码 相关的任务。
  不适用：修 bug、跑起来看（用对应技能）。
---

# 写功能

## 怎么做

1. list_dir / read_file 读懂现有结构和约定
2. 小步改，write_file / edit_file 落盘
3. run_shell 跑起来或跑测试，确认能用
4. 写清改了哪些文件、怎么验证

## 交付

- 用 write_file 把结果写成 Markdown，放在这件事的文件夹里，文件名用中文说清是什么
- 每条事实带出处（网址或文件名）和日期；查不到的写【待补充】，不编数字

## 离线演示

没配模型时系统自动运行 scripts/main.py 返回演示数据；配了模型时不要运行它。
