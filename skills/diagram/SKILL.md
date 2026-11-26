---
name: diagram
description: 把流程、架构、组织关系、项目计划、知识点画成可编辑的图：Mermaid 或 draw.io 文件，外加能直接打开看的 HTML / SVG；海报用 canvas-design，数据图表用 chart-report
license: Apache-2.0
metadata:
  version: 1.0.0
  author: imoling
---

# 流程图和架构图

## 选格式

| 主人要 | 交付 |
|---|---|
| 能改的图、放进文档 | `.drawio`（draw.io 打开可编辑）+ 预览 HTML |
| 写在 Markdown / 飞书 / 语雀里 | Mermaid 代码块 |
| 直接看或贴进 PPT | 单文件 SVG 或 HTML |

没说就交 Mermaid + 预览 HTML。

## 怎么做

1. **先列节点和关系**：节点（≤ 15 个，多了拆成两张图或分组）、连线（谁到谁、上面写什么）、分组（泳道、层）。用三五行写清再画。
2. **选图型**：流程 → 从上到下的流程图，判断用菱形；系统架构 → 分层（接入 / 应用 / 数据 / 基础设施）从上到下；组织 → 树；项目计划 → 甘特图（Mermaid `gantt`）；时序交互 → 时序图。
3. **Mermaid**：写进 `.md`，同时生成预览页（离线，不用 CDN 时就用 SVG）：
   ```html
   <pre class="mermaid">flowchart TD ...</pre>
   <script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs'; mermaid.initialize({startOnLoad:true});</script>
   ```
   要求离线时直接手写 SVG。
4. **draw.io**：写 mxGraph XML（`<mxfile><diagram><mxGraphModel><root>…`），每个节点一个 `mxCell vertex="1"`，给明确的 x / y / width / height，网格对齐（20 的倍数），连线 `edge="1"` 指定 source / target。
5. **样式**：同一层级同一种颜色，最多 3 种主色；文字 ≤ 12 个字一行；箭头方向一致。

## 交付前自查

- 用 check_page 打开预览页，没有报错，`return document.querySelectorAll('svg').length` 大于 0
- 节点名和主人原话一致，没有漏掉主人提到的环节
- 连线没有交叉成一团；交叉多就调整布局或拆图
