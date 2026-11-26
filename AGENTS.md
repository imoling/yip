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
| 一件事 | 主任务（`tasks.parent_id IS NULL`） | 一次要干的活；和 Yip 只有一条对话，「事」只在看板、产物里当分组 |
| 技能 | `skill` | 队员能调的一项能力，`SKILL.md`；队员自带的在包里，公共技能库在 `data/skills/` |
| 连接器 | `mcp` | MCP 服务，配置在 `data/mcp.json`（标准 mcpServers 格式） |
| 等你确认 | `approvals` + `pending_confirm` 的判断 | 输入框上方的抽屉：工具放行请求和超过门槛的推翻 |
| 本次都允许 | `allow_task` / `tasks.grants` | 这件事里同一个工具不再问 |
| 产物 | `artifacts` | 队员写进工作文件夹的文件和汇总文档 |
| 记忆 | `memories`（`server/memory.py`） | Yip 跨对话记得的事：偏好、关于你、经验、结论、进展；「记住…」直接记 |
| 工作文件夹 | `settings.workspace()` | 队员只在这里读写，默认 `~/Documents/Yip` |
| 能力卡片 | `WorkerCard` | 会什么（capabilities）、要什么（需要的输入）、交付标准 |
| 派给 / 改派 | `assign` / `reassign` | 子任务交给哪个队员；改派按推翻的代价规则处理 |
| 本机 | — | 程序跑在用户电脑上 |
| Yip | `yip` | 产品名，对外和界面都只写 Yip，不带 iML 前缀；仓库 imoling/yip；英文 yip 是草原犬鼠站岗时提醒同伴的叫声 |
| 茉莉 | `mascot` | 吉祥物：头戴茉莉花的草原犬鼠，工作时才戴耳机；素材在 `assets/brand/` |

口吻统一用「点一下」，不用「点击」。

## 约束

- worker 是独立 compiled graph，不是 supervisor 的节点
- 判断没返回前不执行动作；队列按 seq 排，不按时间
- 推翻前后 task_id 不变、run_id 变，落 `discarded` 事件
- 模型服务和 Yip 设置存 `data/settings.json`（预置表在 `server/providers.py`），不硬编码，不进 git
- 一句话先过 `router/front.py` 分流（闲聊 / 新的活 / 插话），插话再过 `router/classify.py`
- 队员配了模型走工具调用循环（`server/worker.py` 的 agent 模式），没配走演示脚本；要问人的调用在 gate 节点 `interrupt()`，由 `runner.resume()` 接着跑
- 测试不碰真模型和用户文件夹：`SA_WORKSPACE` 指到缓存目录，假模型见 `tests/test_agent.py`
- 前端只在 Yip 状态胶囊和「正在想」上做轻动效（待命、想、干活、等你确认各不一样，尊重「减少动态效果」），其他地方不做动画
- 运行：桌面版 `cd app && npm run tauri dev`；只跑后端 `.venv/bin/uvicorn server.main:app --port 8770`（托管 `app/dist`，先 `npm run build`）
- 测试：`.venv/bin/pytest -q`；界面改完 `cd app && npx tsc --noEmit`
- 打包：`packaging/build_dmg.sh`（后端 PyInstaller onedir → Tauri sidecar 资源 → dmg）
- 界面在 `app/src`（React + Tailwind），颜色只在 `styles.css` 的变量里定义；`.ring` 等 Tailwind 自带的类名不要拿来当组件类名
