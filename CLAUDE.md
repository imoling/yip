# Yip

1. 删掉多余的标语、说明文字和次要标签。如果删完界面就看不懂，说明布局本身就有问题，直接重新设计。
2. 每次修改都要兼顾用户体验、开发体验和 AI Agent 的使用体验。三个都要变好，而且不能破坏现有功能。

## 需求与计划

以 `docs/需求定稿与开工计划.md` 为准，它覆盖了 `docs/设计参考/` 下的 PDF 文档和 `docs/设计参考/超级助理-harness说明.md` 里互相矛盾的地方。

## 术语表

| 界面上说 | 代码里叫 | 含义 |
|---|---|---|
| 补充 | `augment` | 目标没变，加约束或改路径；融进当前任务，下一轮生效，不丢进度 |
| 追加 | `append` | 目标没变，多了一件事；排队，目标任务跑完后接上 |
| 推翻 | `overturn` | 目标变了；丢掉进度重跑，进度 > 70% 先确认 |
| 判断回执 | `Decision` | 系统对一句插话的理解，人可以改 |
| 改成… | `override` | 人改判断，直接按人指定的动作执行，不重跑分类 |
| 队员 | `worker` | 一个队员包：yip.json + agents/<name>.md + skills/；界面上不说「数字员工」 |
| 一件事 | 主任务（`tasks.parent_id IS NULL`） | 一次要干的活；和 Yip 只有一条对话，「事」只在右栏「进度」、「记录」页和产物里当分组 |
| 技能 | `skill`（`tools/skills.py`） | 标准 Agent Skills 格式（SKILL.md + 参考资料 / 脚本）；技能库 = 内置 `skills/` + 主人装的 `data/skills/`，能从文件夹、zip、GitHub 装，对话里也能装；队员从库里勾，内置队员默认见 `workers/_skills.json`；`demo/` 只是没配模型时的演示数据，不算技能 |
| 连接器 | `mcp` | MCP 服务，配置在 `data/mcp.json`（标准 mcpServers 格式） |
| 等你确认 | `approvals` + `pending_confirm` 的判断 | 输入框上方一次问一件，编号选项（按 1 到 3 选）：工具放行请求和超过门槛的推翻 |
| 模式 | 设置 `approval`（`tools/risk.py`） | 输入框右下角：自动 `auto`（默认，工作文件夹里不问）/ 手动 `ask_writes` / 计划 `plan`（拆完等你说开工）/ 全部放行 `allow_all`；发邮件任何模式都问 |
| 斜杠命令 | `main._slash` | `/新事` `/自动化` `/记住` `/停下` `/计划` `/清屏` |
| 清屏 / 聊天记录 | `server/chatlog.py`、`History.tsx` | 清屏只清界面不删记录：这一段压成前情，Yip 照样记得，细节用 search_history 自己翻；聊天记录照微信，按关键词、文件、图片、链接、日期找 |
| 日常模型 / 难活模型 | 设置 `fast_model` / `model`（`worker.need_pro`） | 队员、聊天、拆活先用日常模型；拆活标了「难」、验收没过返工、连着出错卡住时换难活模型；验收评审用难活模型，图片先给它看，看不了换能看图的 |
| 平时搜索 | 设置 `search.provider`（`tools/web.py`） | 默认 Keenable（免 key，每个 IP 每小时 1000 次），搜不动换 DuckDuckGo；读网页取不到也换 Keenable 抓取 |
| 深度搜索 | Tavily 连接器（预置 `tavily`） | 要挖得深时用；平时的搜失败了也自动换它；连接器在「连接器」页或对话里都能接 |
| 浏览器 | `tools/browser.py` | 队员用的 Yip 专用浏览器（本机 Chrome + 自己的登录资料 data/browser/），右栏「浏览器」看画面；要登录弹窗请主人登 |
| 操作电脑 | `tools/computer.py` | 用「辅助功能」看应用界面、按编号点和打字；任何模式都问，可以选「这件事里的电脑操作都允许」；命令行里的 osascript 在自动模式下也要问 |
| 打码 | `server/setup.py` | 主人在对话里贴的 key 一进来就换成「【密钥 tvly-…a3F9】」，库、模型、记忆里只有占位符；Yip 用聊天里的工具自己写进设置 |
| 干活的方式 | 设置 `engine`（`worker_deep.py`） | 标准 `classic` / Deep Agents（试验）`deep` |
| 本次都允许 | `allow_task` / `tasks.grants` | 这件事里同一个工具不再问 |
| 产物 | `artifacts` | 队员写进工作文件夹的文件和汇总文档 |
| 小队 | `workers/_teams.json` / 偏好里的 `team` | 队员分组，不写进 yip.json |
| 试一下 | 主任务 `kind=trial` | 只派给一个队员的试用活，不进对话、记录和记忆 |
| 自动化 | `automations`（`server/automation.py`） | 到点把一句话交给 Yip，主任务 `kind=auto` |
| 附件 | `uploads`（`server/uploads.py`） | 对话里加的图片和文件，存在工作文件夹/附件/ |
| 记忆 | `memories`（`server/memory.py`） | Yip 跨对话记得的事：偏好、关于你、经验、结论、进展；「记住…」直接记 |
| 性格 / 人设卡 | `server/soul.py`、设置 `yip.soul` | Yip 的灵魂：内置说话和干活规矩（话跟着主人走、不用破折号省略号、干正事人在场、知道几点、纠正一次记成规矩）+ 每个主人自己的人设卡（名字、称呼、性格 干练 / 温和 / 嘴欠心软、关系、表情、自我介绍、白天主动找你）；对话里说一句就能改；只管 Yip 说的话，队员文档照常正式 |
| 工作文件夹 | `settings.workspace()` | 队员只在这里读写，默认 `~/Documents/Yip` |
| 能力卡片 | `WorkerCard` | 会什么（capabilities）、要什么（需要的输入）、交付标准 |
| 派给 / 改派 | `assign` / `reassign` | 子任务交给哪个队员；改派按推翻的代价规则处理 |
| 本机 | — | 程序跑在用户电脑上 |
| Yip | `yip` | 产品名，对外和界面都只写 Yip，不带 iML 前缀；仓库 imoling/yip；英文 yip 是草原犬鼠站岗时提醒同伴的叫声 |
| 验收标准 | `tasks.accept` | 拆活时每块写 2 到 4 条能核对的标准 |
| 验收 / 返工 | `server/verify.py`、worker 图的 `verify` 节点 | 队员说做完先机器检查再模型评审，不过退回返工，最多 2 次 |
| 还差什么 | `graph.goal_check` | 交付前对照主人原话查一遍，没满足的写进汇总和最后一条消息 |
| 小队频道 / 留言 | `server/team.py`、`team_msgs`、工具 `team_post` | 同一件事的队员互相传话；每轮系统提示里带队友实时状态，新留言下一轮插进对话 |
| 退回 / 复查 | 工具 `send_back`、`runner.reopen` / `hand_back` | 测试、审查把问题退回给前面的人返工，改完自动叫退回的人复查；每人最多被退回 2 次，下游等复查完才开工 |
| 考题 | `evals/cases.jsonl`、`scripts/run_evals.py` | 真模型跑典型的活并自动判分，报告在 `evals/results/` |
| 茉莉 | `mascot` | 吉祥物：头戴茉莉花的草原犬鼠，工作时才戴耳机；素材在 `assets/brand/`，四段状态动画的原视频在 `assets/brand/anim/`，压缩后给胶囊头像用的在 `app/src/assets/anim/`（256 像素正放加倒放循环） |

口吻统一用「点一下」，不用「点击」。

## 约束

- worker 是独立 compiled graph，不是 supervisor 的节点
- 判断没返回前不执行动作；队列按 seq 排，不按时间
- 推翻前后 task_id 不变、run_id 变，落 `discarded` 事件
- 模型服务和 Yip 设置存 `data/settings.json`（预置表在 `server/providers.py`），不硬编码，不进 git
- 一句话先过 `router/front.py`：有模型时 Yip 用工具决定（回答 / 派活 / 建自动化 / 插话），没模型走规则；插话再过 `router/classify.py`
- 改了拆活、队员提示词、验收这些影响交付质量的地方，跑一遍 `.venv/bin/python scripts/run_evals.py`，把通过率写进说明
- 队员配了模型走工具调用循环（`server/worker.py` 的 agent 模式），没配走演示脚本；要问人的调用在 gate 节点 `interrupt()`，由 `runner.resume()` 接着跑
- 测试不碰真模型和用户文件夹：`SA_WORKSPACE` 指到缓存目录，假模型见 `tests/test_agent.py`
- 对话里像人一样说话：派活一句话、接力和退回各冒一句、交付带附件；进度卡和插话判断只在右栏「进度」里，不进对话；做完的事在左侧「记录」页，一件事一行
- 前端只在 Yip 状态胶囊和「正在想」上做轻动效（待命、想、干活、等你确认各不一样，尊重「减少动态效果」），其他地方不做动画
- 运行：桌面版 `cd app && npm run tauri dev`；只跑后端 `.venv/bin/uvicorn server.main:app --port 8770`（托管 `app/dist`，先 `npm run build`）
- 测试：`.venv/bin/pytest -q`；界面改完 `cd app && npx tsc --noEmit`
- 打包：`packaging/build_dmg.sh`（后端 PyInstaller onedir → Tauri sidecar 资源 → dmg）
- 界面在 `app/src`（React + Tailwind），颜色只在 `styles.css` 的变量里定义；`.ring` 等 Tailwind 自带的类名不要拿来当组件类名
