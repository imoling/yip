---
name: chart-report
description: 把数据做成能离线打开的交互式 HTML 图表报告（指标卡 + 图表 + 明细表），讲趋势、对比、占比、排名；只要一张表用 data-table，要放进 PPT 的静态图用 run_shell 画 PNG
license: Apache-2.0
metadata:
  version: 1.0.0
  author: imoling
---

# 图表报告

技能目录 `assets/echarts.min.js` 是 Apache ECharts 5（Apache-2.0），拷进报告文件夹就能离线用，不依赖 CDN。

## 怎么做

1. **读数据**：read_file 读 Excel / CSV；数据要查的先查并记出处。用 run_shell 跑 Python 算好要画的数，存成一份 JSON，别在 HTML 里手算。
2. **选图**（一个问题一张图，最多 6 张）：
   | 想说明 | 用 |
   |---|---|
   | 随时间变化 | 折线 |
   | 几类比大小、排名 | 横向柱状（按值排序） |
   | 构成占比（≤6 类） | 环形图；类多了用堆叠柱 |
   | 两个指标的关系 | 散点 |
   | 目标完成度 | 仪表或进度条 |
3. **搭页面**：在这件事的文件夹里建 `图表报告/`，`cp <技能目录>/assets/echarts.min.js 图表报告/`，写 `图表报告/index.html`：
   - 顶部：标题、数据时间范围、一句话结论
   - 指标卡一行 3 到 4 个：数值、单位、比上期变化（涨绿跌红，或按业务含义）
   - 每张图上方一句话写这张图说明了什么，不是写「XX 统计图」
   - 底部：明细表和数据来源
   - `<script src="echarts.min.js"></script>`，数据直接内联成 JS 常量
   - 图表随窗口缩放：`window.addEventListener('resize', () => charts.forEach(c => c.resize()))`
4. **样式**：白底，一个主色加灰色，同一类数据整页同色；中文字体用系统字体；数字千分位。

## 交付前自查

用 check_page 打开 index.html：
- console 没报错（`echarts is not defined` 说明 js 没拷进去）
- 脚本里 `return document.querySelectorAll('canvas').length` 和图的数量一致
- 图上的数和明细表一致

最后回复：一句话结论 + 文件在哪（双击 index.html 就能看）。
