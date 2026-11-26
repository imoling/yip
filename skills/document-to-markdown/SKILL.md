---
name: document-to-markdown
description: 把 Word、PPT、Excel、PDF、HTML 或网页转成保留标题层级和表格的 Markdown，给后续整理、改写、比对用；只想看一眼文字用 read_file 就够
license: Apache-2.0
metadata:
  version: 1.0.0
  author: imoling
---

# 文件转 Markdown

用微软开源的 MarkItDown（MIT），标题、列表、表格都能留住，比 read_file 抽出来的纯文字好整理。

## 怎么做

1. 找 uvx：`command -v uvx || ls ~/.local/bin/uvx`。都没有就退回 read_file，并在回复里说明「没装 uv，转出来的没有表格结构」。
2. 转换，结果写进这件事的文件夹：
   ```bash
   UVX=$(command -v uvx || echo ~/.local/bin/uvx)
   $UVX --from 'markitdown[all]' markitdown "原文件.docx" -o "转换/原文件.md"
   ```
   网页：把文件名换成网址。第一次运行会下载依赖，要一两分钟。
3. 一批文件就循环转，转完 `ls -la 转换/` 看看每个 .md 是不是都有内容（0 字节的说明没转成功）。
4. 用 read_file 读回来抽查：标题层级对不对、表格有没有错位。扫描版 PDF 转出来是空的，要在回复里点出来，别当成转好了。

## 注意

- 不改原文件，转出的 .md 另存。
- 转出来的东西是中间材料，不要当最终交付；主人要的是摘要、比对或改写，就接着做完。
