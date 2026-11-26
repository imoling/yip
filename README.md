<!--
  图片写成 jsDelivr 外链，不用相对路径：相对路径会被 GitHub 发到 raw.githubusercontent.com，
  国内多数网络下这个域名被 DNS 污染，访客看到碎图；外链会改走 camo.githubusercontent.com，直连可达。
  代价：@main 有缓存，换图最长半天才刷新。图放在 assets/readme/，别改回相对路径。
-->

<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/icon.png" width="128" alt="Yip">
</p>

<h1 align="center">Yip</h1>

<p align="center"><b>一个简单 personal agent，你的超级 AI 助理和 TA 的朋友们<br>像跟真人帮你办事，你可以随时插话，有事先问，做完汇报</b></p>

<p align="center">
  <a href="LICENSE"><img src="https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/badge-license.svg" alt="Apache-2.0"></a>
  <img src="https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/badge-version.svg" alt="0.5.0">
  <img src="https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/badge-platform.svg" alt="macOS">
</p>

<p align="center">作者：imoling（imoling.cn@gmail.com）</p>

![一件事拆给三位队员同时做，中途补充一句、追加一件，都没打断](https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/interject.jpg)

<p align="center"><em>一件事拆成三块同时做；中途说「只看国内」并进当前任务，「顺便列免费额度」排到后面接上</em></p>

和 Yip 只有一条对话。闲聊、提问它直接答；交代一件事，它拆成几块，派给对口的队员同时做。队员会真的去搜索、读网页、读写工作文件夹里的文件、跑命令、调连接器，做完汇总成一份文档放回对话里。

## 插话：补充、追加、推翻

做事做到一半想改主意，是最常见的情况。Yip 每收到一句插话，先判断它属于哪一种，再动手：

| 判断 | 什么时候 | 怎么做 |
|---|---|---|
| 补充 | 目标没变，加约束、收窄范围、改格式 | 约束写进当前任务，下一轮生效，不打断、不丢进度 |
| 追加 | 目标没变，多了一件原来没有的事 | 新建一个排队的子任务，前面的跑完按顺序接上 |
| 推翻 | 目标变了或方向被否定 | 停掉这次运行、丢掉进度、带新目标重跑；进度超过 70% 先问一句 |

判断先问快模型（3 秒超时），没配模型、连不上或超时就用本地规则，回执上会标「本地规则」。拿不准的一律先排着。判错了，回执旁点一下「改成…」，按你指定的动作执行。

![做完汇总成一份文档，插话的处理记录留在对话里](https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/done.jpg)

## 队员

<img src="https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/team.jpg" width="240" align="right" alt="茉莉和队员">

内置 14 个队员，分三个小队：

- 调研小队：情报调研员、数据分析员、报告撰写员
- 软件开发小队：需求分析师、开发工程师、测试工程师、代码审查员
- 办公小队：通用助理、邮件助理、会议秘书、文书专员、汇报助理、PPT 设计师、文件管家

日常模型打底，难的活（多文件的软件、要多方核对的调研）或者做不动了，换难活模型。软件、网页和难活交付前先过机器检查和模型评审，不过就退回返工；其余的活机器检查过了先交给你，模型评审在后台接着做，有问题再退回改好、交一版新的。

也可以自建或导入队员，新建后点「试一下」，只派给它跑一件小事，不进对话和记忆。

还可以把本机已安装的外部 Agent 接成 **外部队员**（Antigravity、Codex、Pi、Hermes、WorkBuddy、Grok Bot、OpenClaw、Claude Code、Muse），通过官方 ACP 协议或原生命令行调度，不碰任何订阅凭证；在对话框输入 `@队员名` 可直接指定某位队员干活，说「不用了」「停下」可直接取消当前任务。

<br clear="right">

## 跑起来

桌面版（macOS）：

```bash
git clone https://github.com/imoling/yip.git && cd yip
uv venv --python 3.12 .venv
uv pip install -r requirements.txt --python .venv/bin/python
cd app && npm install && npm run tauri dev
```

左下角的设置里选模型服务、填密钥。不填也能跑：插话判断和派活走本地规则，队员跑演示数据。

只用浏览器：

```bash
cd app && npm run build && cd ..
.venv/bin/uvicorn server.main:app --port 8770
```

打开 http://127.0.0.1:8770 。改界面时另开一个终端 `cd app && npm run dev`，用 http://localhost:1420（`/api` 会转给 8770）。

<details>
<summary>打安装包、日志、演示调速</summary>

```bash
uv pip install --python .venv/bin/python pyinstaller
packaging/build_dmg.sh
```

产物在 `app/src-tauri/target/release/bundle/dmg/`。没有签名，第一次打开要在「访达」里右键 Yip，选「打开」。

- 桌面版日志：`~/Library/Application Support/cn.imoling.yip/logs/server.log`；开发时数据在仓库的 `data/`
- 每轮步骤默认 2–4 秒，演示时想慢一点：`SA_STEP_DELAY=4,6 .venv/bin/uvicorn …`

</details>

## 进度和记录

右栏「进度」里一件事一张卡，看得到每位队员在做哪一块、做到哪；做完的事收进左侧「记录」，能按事、队员、文件搜。

![右栏进度：一件事一张卡，每位队员一行](https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/progress.jpg)

![记录页：做完的事按时间排开，能按事、队员、文件搜](https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/records.jpg)

## 技能

技能用标准的 `SKILL.md` 格式。内置 14 个（调研报告、公文通知、会议纪要、按大纲做 PPT、图表报告、流程图等），也能装自己的：Claude Code、Hermes 的技能文件夹直接装，GitHub 链接、zip 也行，或者在对话里跟 Yip 说「把 ~/.claude/skills 里的 xx 装上」。说一句话，Yip 也能起草一个新技能。装好的技能在队员那里勾上就能用。

![技能库](https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/skills.jpg)

## 性格

Yip 有一套说话和干活的规矩，再加一张人设卡：名字、怎么称呼你、性格（干练 / 温和 / 嘴欠心软）、你们的关系、用不用表情。对话里说一句就能改，纠正一次就记住。可以打开「工作日白天偶尔主动找我」，一天最多两次，晚上和周末不打扰。

![设置 · 性格](https://cdn.jsdelivr.net/gh/imoling/yip@main/assets/readme/soul.jpg)

## 能做什么

- **模型服务**：预置 DeepSeek、讯飞星火、通义千问、智谱 GLM、Kimi、豆包、MiniMax、OpenAI、OpenRouter、Ollama（本机）和自定义；主模型拆活、干活、汇总，快模型判断插话，难活可以单独配一个更强的模型
- **Office 文件**：要 PPT、Word、Excel、PDF 的，直接交真文件；PPT 的讲稿写在备注里
- **浏览器**：队员用本机的 Chrome 打开网页、点、填、读，登录一次以后一直有效；平时在后台跑，右栏能看到它的画面
- **操作电脑**（macOS）：通过「辅助功能」读前台应用的按钮和输入框，按名字点、打字、按快捷键；每一步都先问你
- **搜索**：默认用 Keenable，不用密钥；要深度搜索就接 Tavily 连接器。在对话里贴的 key 会自动打码
- **等你确认**：只读的动作直接做；写文件、跑命令、调连接器先在输入框上方问你，可选「本次都允许」；「设置 · 通用」里能改成全部放行或全部要问
- **连接器**：MCP 服务，配置在 `data/mcp.json`，格式和 Claude Desktop、Cursor 一样，可以整段粘贴
- **附件**：图片和文件可以点回形针、拖进来或粘贴，存到「工作文件夹/附件/」；txt、md、csv、docx、xlsx、pdf 先抽成文字
- **邮件**：队员能搜信、读信、起草；发信每封都先问你，「全部放行」也一样
- **定时自动化**：到点把一句话交给 Yip；关掉窗口 Yip 留在菜单栏继续跑
- **记忆**：说「记住…」直接记；偏好、经验、结论跨对话带着
- **清屏和聊天记录**：清屏只是让对话区清爽，之前的话在「聊天记录」里，Yip 也记得

## 队员包格式

一个队员是一个包含清单、人设和技能的目录（支持 zip 导入）：

```
<包根>/
├─ yip.json                      # name / type / version / introduction / personality / capabilities / prompts
└─ <agent-id>/
   ├─ agents/<agent-id>.md       # 身份、工作方式、能力路由、需要的输入、交付标准、安全规则
   └─ skills/<skill-id>/
      ├─ SKILL.md
      └─ scripts/main.py         # 可选
```

内置队员在 `workers/`，由 `scripts/build_builtin_workers.py` 生成；小队写在 `workers/_teams.json`；自建和导入的在 `data/workers/`。

## 测试

```bash
.venv/bin/pytest -q                                   # 规则、三个动作、队员包、agent loop、连接器、界面文案
.venv/bin/python scripts/eval_classify.py             # 30 条插话测试集，用设置里配的模型
.venv/bin/python scripts/eval_classify.py --rules     # 只用本地规则
.venv/bin/python scripts/run_evals.py                 # 真模型跑典型的活并自动判分
```

术语表和开发约定见 [`CLAUDE.md`](CLAUDE.md)。

## 许可

[Apache-2.0](LICENSE)。可以商用、可以改；转发或基于本项目做产品，须保留 [`NOTICE`](NOTICE) 里的署名，并在改过的文件里标明改动。吉祥物茉莉和应用图标的权属归作者，不随代码授权。
