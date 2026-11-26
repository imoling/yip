"""外部队员（第三方本地 Agent）全量目录、本机探测与安全配置存储。

安全红线：
- 只驱动本机未修改的官方 CLI / ACP 进程，绝不读取、存储或转发任何厂商的 OAuth Cookie / Token。
- 配置文件 data/external_agents.json 权限 0600，不进 git；密钥对外一律脱敏。
- 每个外部队员默认并发限制为 1，防止自动化高频请求触发厂商限流或风控。
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import db
from .workers.package import Skill, WorkerCard

MASK = "••••••"

EXTRA_BIN_DIRS = (
    Path.home() / ".local" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path.home() / ".hermes" / "bin",
    Path.home() / ".npm-global" / "bin",
)


@dataclass(frozen=True)
class ExternalSpec:
    id: str
    name: str
    protocol: str               # acp / openapi
    status: str                 # ready / planned
    introduction: str
    capabilities: list[dict]
    inputs: str
    deliverable: str
    personality: list[str]
    binary: str                 # 主命令（本机探测用）
    default_cmd: list[str]      # 默认启动命令
    alt_binary: str = ""        # 备选直连命令（如 codex-acp / pi-acp）
    app_bins: tuple[str, ...] = ()  # 桌面客户端内置命令路径候选
    install_cmd: str = ""       # 官方标准安装命令（白名单，用于一键协助安装）
    env_key: str = ""           # 密钥环境变量名
    default_auth: str = "cli"   # cli / api_key
    safety_note: str = ""
    install_hint: str = ""
    avatar: str = "coder"
    max_concurrency: int = 1


SPECS: list[ExternalSpec] = [
    ExternalSpec(
        id="ext-agy",
        name="Antigravity",
        protocol="acp",
        status="ready",
        introduction="Google Antigravity 编程智能体，擅长多文件工程、重构、调试与端到端实现。",
        capabilities=[{
            "name": "工程研发与调试",
            "description": "使用本机 Antigravity ACP 引擎完成代码开发、重构、测试与排错",
            "skills": [{"id": "acp-coding", "name": "全栈工程实现", "description": "读写项目代码、执行测试并交付可运行结果"}],
        }],
        inputs="明确的需求目标、项目路径或代码上下文，以及约束条件",
        deliverable="可运行的代码改动、测试结果与 3 到 6 行改动说明",
        personality=["工程严谨", "擅长多文件修改", "自动验证结果"],
        binary="agy",
        default_cmd=["agy", "acp"],
        env_key="GEMINI_API_KEY",
        default_auth="cli",
        safety_note="仅调用本机官方 agy acp 进程，不读取或代理任何登录凭证；重度自动化建议改填密钥。",
        install_hint="需本机已安装 agy（1.5+ 版本）并完成 agy login",
        avatar="ext-agy",
    ),
    ExternalSpec(
        id="ext-hermes",
        name="Hermes",
        protocol="acp",
        status="ready",
        introduction="Nous Research 开源自主智能体，擅长深度调研、脚本自动化与持久化记忆协作。",
        capabilities=[{
            "name": "自主研发与自动化",
            "description": "使用本机 Hermes ACP 引擎完成调研、脚本编写与多步工程任务",
            "skills": [{"id": "acp-auto", "name": "自主任务执行", "description": "结合终端、文件与技能自主拆解并交付"}],
        }],
        inputs="任务目标、资料范围或脚本要求",
        deliverable="结构化文档或可运行脚本，以及核对过的结论摘要",
        personality=["开源无封号风险", "自主拆解", "善于沉淀经验"],
        binary="hermes",
        default_cmd=["hermes", "acp"],
        install_cmd="uv tool install hermes-agent",
        env_key="OPENROUTER_API_KEY",
        default_auth="cli",
        safety_note="开源模型无关框架，无平台封号风险；可沿用本机 hermes 配置或单独填入密钥。",
        install_hint="需本机已安装 hermes 并可用 hermes acp --check 通过",
        avatar="ext-hermes",
    ),
    ExternalSpec(
        id="ext-codex",
        name="Codex",
        protocol="cli",
        status="ready",
        introduction="OpenAI Codex 编程智能体，擅长高质量代码生成、单元测试与精确补丁修复。",
        capabilities=[{
            "name": "代码生成与审查",
            "description": "直接调用本机官方 codex exec 引擎完成编码、测试与修 Bug",
            "skills": [{"id": "acp-codex", "name": "精准编码与测试", "description": "按规格写代码、补测试并排查缺陷"}],
        }],
        inputs="功能规格、报错信息或目标代码文件",
        deliverable="干净的代码改动、测试通过记录与简要说明",
        personality=["代码干净", "重视单测", "遵守约束"],
        binary="codex",
        default_cmd=["codex", "exec", "--skip-git-repo-check", "-"],
        install_cmd="npm install -g @openai/codex",
        env_key="OPENAI_API_KEY",
        default_auth="api_key",
        safety_note="推荐填入密钥以零风险运行；也可复用本机官方 Codex 登录态（仅限个人交互使用）。",
        install_hint="需本机已安装 codex（npm install -g @openai/codex）",
        avatar="ext-codex",
    ),
    ExternalSpec(
        id="ext-pi",
        name="Pi",
        protocol="cli",
        status="ready",
        introduction="极简高可扩展开源编程智能体，原生树状会话、超轻量工具循环，零平台锁定。",
        capabilities=[{
            "name": "极简高效编程",
            "description": "直接调用本机原生 pi -p 引擎执行读写改文件与终端命令",
            "skills": [{"id": "acp-pi", "name": "轻量代码实现", "description": "快速改代码、跑命令并输出简洁结果"}],
        }],
        inputs="任务目标、代码路径与验证方式",
        deliverable="精简可靠的代码改动与验证结果",
        personality=["极简高效", "开源无封号风险", "工具干净"],
        binary="pi",
        default_cmd=["pi", "-p", "--no-session"],
        install_cmd="npm install -g @mariozechner/pi-coding-agent",
        env_key="OPENAI_API_KEY",
        default_auth="cli",
        safety_note="开源框架无封号风险，复用 ~/.pi/agent 本机配置或直接填入密钥均可。",
        install_hint="需本机已安装 pi（npm install -g @mariozechner/pi-coding-agent）",
        avatar="ext-pi",
    ),
    ExternalSpec(
        id="ext-workbuddy",
        name="WorkBuddy",
        protocol="acp",
        status="ready",
        introduction="腾讯 WorkBuddy / CodeBuddy 智能体，原生支持 ACP 协议与国内办公研发协同。",
        capabilities=[{
            "name": "办公与研发协同",
            "description": "通过原生 codebuddy --acp 接入腾讯智能体能力",
            "skills": [{"id": "acp-workbuddy", "name": "企业协同与研发", "description": "结合文档、工具与代码能力交付"}],
        }],
        inputs="任务目标、业务背景或代码仓库上下文",
        deliverable="文档、方案或可运行代码产出",
        personality=["贴合国内办公", "原生 ACP", "工具丰富"],
        binary="codebuddy",
        default_cmd=["codebuddy", "--acp"],
        install_cmd="npm install -g @tencent-ai/codebuddy-code",
        env_key="CODEBUDDY_API_KEY",
        default_auth="api_key",
        safety_note="需独立安装官方 CLI（WorkBuddy 桌面端内置脚本不可独立调用），推荐搭配官方密钥使用。",
        install_hint="需安装独立 CLI：npm install -g @tencent-ai/codebuddy-code",
        avatar="ext-workbuddy",
    ),
    ExternalSpec(
        id="ext-grok",
        name="Grok Bot",
        protocol="acp",
        status="ready",
        introduction="xAI Grok Build 终端智能体，具备高速推理、代码编写与原生 ACP 接入能力。",
        capabilities=[{
            "name": "高速推理与编码",
            "description": "通过官方 grok agent stdio 接入 xAI Grok Build 引擎",
            "skills": [{"id": "acp-grok", "name": "快速分析与实现", "description": "快速定位问题、编写代码并验证"}],
        }],
        inputs="技术问题、功能需求或待排查的日志与代码",
        deliverable="明确结论、代码改动与验证说明",
        personality=["响应极快", "直奔重点", "擅长推理"],
        binary="grok",
        default_cmd=["grok", "agent", "stdio"],
        install_cmd="npm install -g @xai-official/grok",
        env_key="XAI_API_KEY",
        default_auth="api_key",
        safety_note="推荐使用 XAI_API_KEY 密钥模式零风险运行；也可沿用本机官方 grok login。",
        install_hint="需本机已安装 @xai-official/grok（grok 命令）",
        avatar="ext-grok",
    ),
    ExternalSpec(
        id="ext-openclaw",
        name="OpenClaw",
        protocol="acp",
        status="ready",
        introduction="开源本地 Agent 网关，支持多通道协作、丰富技能生态与沙箱执行。",
        capabilities=[{
            "name": "多通道与本地自动化",
            "description": "通过原生 openclaw acp 桥接本机网关执行任务",
            "skills": [{"id": "acp-openclaw", "name": "网关协同", "description": "调用 OpenClaw 技能与沙箱执行任务"}],
        }],
        inputs="自动化目标与上下文",
        deliverable="执行结果与产物文件",
        personality=["生态丰富", "本地优先", "可编排"],
        binary="openclaw",
        default_cmd=["openclaw", "acp"],
        install_cmd="npm install -g openclaw",
        env_key="",
        default_auth="cli",
        safety_note="开源框架本身无封号风险，请勿在 OpenClaw 内挂载第三方消费订阅登录插件。",
        install_hint="需本机已安装 openclaw 且 Gateway 正常运行",
        avatar="ext-openclaw",
    ),
    ExternalSpec(
        id="ext-claude-code",
        name="Claude Code",
        protocol="cli",
        status="ready",
        introduction="Anthropic 官方编程智能体，擅长复杂架构重构与长上下文代码理解。",
        capabilities=[{
            "name": "复杂代码工程",
            "description": "直接调用本机官方 claude -p 引擎完成架构与代码重构",
            "skills": [{"id": "acp-claude", "name": "架构与重构", "description": "跨模块深度分析与重构"}],
        }],
        inputs="仓库上下文与重构目标",
        deliverable="代码修改与验证说明",
        personality=["推理深入", "代码审美高", "严格按规范执行"],
        binary="claude",
        default_cmd=["claude", "-p"],
        install_cmd="npm install -g @anthropic-ai/claude-code",
        env_key="ANTHROPIC_API_KEY",
        default_auth="api_key",
        safety_note="Anthropic 对订阅凭证风控极严，强烈建议仅使用 ANTHROPIC_API_KEY 密钥模式。",
        install_hint="需本机已安装 claude（npm install -g @anthropic-ai/claude-code）",
        avatar="ext-claude-code",
    ),
    ExternalSpec(
        id="ext-muse",
        name="Muse",
        protocol="acp",
        status="ready",
        introduction="Meta Muse / Muse Code 编程智能体，支持沙箱多步规划与 muse-acp 协议桥接。",
        capabilities=[{
            "name": "代码与自主执行",
            "description": "通过 muse-acp 桥接 Meta Muse Code 完成多步编程任务",
            "skills": [{"id": "acp-muse", "name": "终端编程与推理", "description": "在沙箱内多步规划、修改代码并验证"}],
        }],
        inputs="需求说明或代码仓库上下文",
        deliverable="代码修改与执行结果说明",
        personality=["长步规划", "原生沙箱", "推理深入"],
        binary="muse",
        default_cmd=["muse-acp"],
        alt_binary="muse-acp",
        install_cmd="curl -fsSL https://dev.meta.ai/install.sh | bash",
        env_key="META_API_KEY",
        default_auth="api_key",
        safety_note="推荐通过 muse-acp 与 META_API_KEY 密钥接入，不碰消费端登录态。",
        install_hint="需本机已安装 muse 与 muse-acp",
        avatar="ext-muse",
    ),
    ExternalSpec(
        id="ext-doubao-work",
        name="豆包工作",
        protocol="acp",
        status="planned",
        introduction="字节跳动豆包工作 AI Agent，面向自动化办公与多步任务执行（待开放本地接入协议后集成）。",
        capabilities=[{
            "name": "自动化办公协同",
            "description": "接入豆包工作 Agent 执行文档、调研与多步办公任务",
            "skills": [{"id": "ext-doubao", "name": "办公任务执行", "description": "向豆包工作派发任务并回收结果"}],
        }],
        inputs="任务目标、业务资料与要求",
        deliverable="整理后的文档与执行结果",
        personality=["办公协同", "多步执行", "本地云端联动"],
        binary="",
        default_cmd=[],
        env_key="",
        default_auth="cli",
        safety_note="仅待官方开放本地协议或接口后合规接入，不做逆向或模拟点击。",
        install_hint="暂未开发",
        avatar="ext-doubao-work",
    ),
]

BY_ID: dict[str, ExternalSpec] = {s.id: s for s in SPECS}


def _path() -> Path:
    return db.DATA_DIR / "external_agents.json"


def load_config() -> dict[str, dict]:
    p = _path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text("utf-8"))
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_config(data: dict[str, dict]) -> None:
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(_path(), 0o600)


def _find_bin(name: str) -> str:
    if not name:
        return ""
    p = Path(name).expanduser()
    if p.is_absolute() or os.sep in name:
        return str(p) if p.is_file() and os.access(p, os.X_OK) else ""
    found = shutil.which(name)
    if found:
        return found
    for d in EXTRA_BIN_DIRS:
        cand = d / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return ""


def probe(spec_id: str, custom_cmd: str = "") -> dict:
    """探测本机是否安装了该外部 Agent 的可执行命令（只查路径，不执行任意代码）。"""
    spec = BY_ID.get(spec_id)
    if not spec:
        return {"installed": False, "path": "", "cmd": [], "reason": "未知外部队员"}
    if spec.status != "ready":
        return {"installed": False, "path": "", "cmd": [], "reason": "暂未开发"}

    raw_cmd = (custom_cmd or "").strip()
    if raw_cmd:
        parts = shlex.split(raw_cmd)
        if not parts:
            return {"installed": False, "path": "", "cmd": [], "reason": "启动命令为空"}
        exe = _find_bin(parts[0])
        if not exe:
            return {"installed": False, "path": "", "cmd": [], "reason": f"找不到命令：{parts[0]}"}
        return {"installed": True, "path": exe, "cmd": [exe, *parts[1:]], "reason": "已检测到自定义命令"}

    if spec.alt_binary:
        alt = _find_bin(spec.alt_binary)
        if alt:
            return {"installed": True, "path": alt, "cmd": [alt], "reason": f"已检测到 {spec.alt_binary}"}

    main_bin = _find_bin(spec.binary)
    if not main_bin:
        for cand in spec.app_bins:
            found = _find_bin(cand)
            if found:
                main_bin = found
                break
    if not main_bin:
        return {"installed": False, "path": "", "cmd": [], "reason": f"本机未检测到 {spec.binary}"}

    if spec.default_cmd and spec.default_cmd[0] == spec.binary:
        from_app = any(main_bin == c for c in spec.app_bins)
        return {"installed": True, "path": main_bin, "cmd": [main_bin, *spec.default_cmd[1:]],
                "reason": "已检测到客户端内置命令" if from_app else "已检测到本机命令"}

    if spec.default_cmd:
        runner_bin = _find_bin(spec.default_cmd[0])
        if not runner_bin:
            return {"installed": False, "path": main_bin, "cmd": [], "reason": f"缺少运行工具：{spec.default_cmd[0]}"}
        return {"installed": True, "path": main_bin, "cmd": [runner_bin, *spec.default_cmd[1:]], "reason": "已检测到本机命令"}

    return {"installed": False, "path": "", "cmd": [], "reason": "未配置启动命令"}


def get_entry(spec_id: str) -> dict:
    cfg = load_config().get(spec_id) or {}
    spec = BY_ID[spec_id]
    return {
        "enabled": bool(cfg.get("enabled", False)) if spec.status == "ready" else False,
        "auth_mode": cfg.get("auth_mode") or spec.default_auth,
        "api_key": str(cfg.get("api_key") or ""),
        "command": str(cfg.get("command") or ""),
        "checked_ok": bool(cfg.get("checked_ok", False)),
        "check_msg": str(cfg.get("check_msg") or ""),
        "checked_at": int(cfg.get("checked_at") or 0),
    }


def public_item(spec: ExternalSpec) -> dict:
    cfg = get_entry(spec.id)
    pr = probe(spec.id, cfg["command"])
    return {
        "id": spec.id,
        "name": spec.name,
        "protocol": spec.protocol,
        "status": spec.status,
        "introduction": spec.introduction,
        "avatar": spec.avatar,
        "binary": spec.binary,
        "env_key": spec.env_key,
        "default_auth": spec.default_auth,
        "safety_note": spec.safety_note,
        "install_hint": spec.install_hint,
        "install_cmd": spec.install_cmd,
        "enabled": cfg["enabled"],
        "auth_mode": cfg["auth_mode"],
        "has_key": bool(cfg["api_key"]),
        "api_key": MASK if cfg["api_key"] else "",
        "command": cfg["command"],
        "installed": pr["installed"],
        "path": pr["path"],
        "probe_reason": pr["reason"],
        "checked_ok": cfg["checked_ok"],
        "check_msg": cfg["check_msg"],
        "checked_at": cfg["checked_at"],
    }


def list_all() -> list[dict]:
    return [public_item(s) for s in SPECS]


def update(spec_id: str, body: dict) -> dict:
    spec = BY_ID.get(spec_id)
    if not spec:
        raise KeyError(spec_id)
    if spec.status != "ready":
        raise ValueError("该外部队员暂未开发")
    data = load_config()
    cur = get_entry(spec_id)
    if "auth_mode" in body and body["auth_mode"] in ("cli", "api_key"):
        cur["auth_mode"] = body["auth_mode"]
    if "command" in body:
        new_cmd = str(body["command"] or "").strip()
        if new_cmd != cur["command"]:
            cur["command"] = new_cmd
            cur["checked_ok"] = False
            cur["check_msg"] = ""
    if "api_key" in body:
        raw_key = str(body["api_key"] or "").strip()
        if raw_key != MASK:
            cur["api_key"] = raw_key
    if "enabled" in body:
        want = bool(body["enabled"])
        if want:
            pr = probe(spec_id, cur["command"])
            if not pr["installed"]:
                raise ValueError(f"无法上岗：{pr['reason']}，请先安装或配置启动命令")
        cur["enabled"] = want
    data[spec_id] = cur
    _save_config(data)
    return public_item(spec)


async def check_connection(spec_id: str, custom_cmd: str | None = None) -> dict:
    """真正拉起外部 ACP 子进程做一次 initialize 握手，验证可正常启动与通信（不消耗模型额度）。"""
    spec = BY_ID.get(spec_id)
    if not spec:
        raise KeyError(spec_id)
    if spec.status != "ready":
        raise ValueError("该外部队员暂未开发")

    cur = get_entry(spec_id)
    cmd_str = cur["command"] if custom_cmd is None else str(custom_cmd or "").strip()
    pr = probe(spec_id, cmd_str)
    if not pr["installed"] or not pr["cmd"]:
        ok, msg = False, pr["reason"]
    elif spec.protocol == "cli":
        # 原生 CLI 模式：直接运行 --version 验证二进制完整可用，毫秒级返回
        exe = pr["cmd"][0]
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "--version",
                env=runtime_env(spec_id),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            lines = [ln.strip() for ln in raw.decode("utf-8", errors="replace").splitlines()
                     if ln.strip() and not ln.strip().startswith("WARNING:")]
            ver = lines[-1][:80] if lines else ""
            if proc.returncode == 0:
                ok, msg = True, f"连通正常（{ver or spec.binary}）"
            else:
                ok, msg = False, f"命令异常退出（{proc.returncode}）：{ver[:160]}"
        except asyncio.TimeoutError:
            ok, msg = False, "检测超时：命令未在 10 秒内响应"
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"检测失败：{str(e)[:200]}"
    else:
        from . import settings, worker_acp
        ws = settings.workspace().resolve()
        ws.mkdir(parents=True, exist_ok=True)
        client = worker_acp.ACPClient(pr["cmd"], ws, runtime_env(spec_id))
        try:
            await client.start()
            res = await asyncio.wait_for(client.initialize(), timeout=20.0)
            await asyncio.wait_for(client.new_session(), timeout=20.0)
            ver = (res or {}).get("protocolVersion", 1)
            ok, msg = True, f"连通正常（ACP 协议 v{ver}）"
        except asyncio.TimeoutError:
            ok, msg = False, "连接超时：命令未在 20 秒内响应 ACP 握手，请检查是否需要登录或更换启动命令"
        except Exception as e:  # noqa: BLE001
            raw_err = str(e)
            if "auth" in raw_err.lower() or "login" in raw_err.lower() or "credential" in raw_err.lower():
                hint = f"请先在终端登录 {spec.binary}，或在下方填入 {spec.env_key}" if spec.env_key else f"请先在终端完成 {spec.binary} 登录"
                ok, msg = False, f"尚未认证（{raw_err[:120]}）：{hint}"
            else:
                ok, msg = False, f"握手失败：{raw_err[:200]}"
        finally:
            await client.close()

    data = load_config()
    cur["checked_ok"] = ok
    cur["check_msg"] = msg
    cur["checked_at"] = int(time.time() * 1000)
    data[spec_id] = cur
    _save_config(data)
    return {"ok": ok, "message": msg, "item": public_item(spec)}


async def assist_install(spec_id: str) -> dict:
    """仅执行白名单里的官方标准安装命令，不接受外部传入任意命令。"""
    spec = BY_ID.get(spec_id)
    if not spec:
        raise KeyError(spec_id)
    if spec.status != "ready":
        raise ValueError("该外部队员暂未开发")
    if not spec.install_cmd:
        raise ValueError(f"{spec.name} 请通过官方安装包安装：{spec.install_hint}")

    env = runtime_env(spec_id)
    if spec.install_cmd.startswith("npm ") and "npm_config_registry" not in env:
        env["npm_config_registry"] = "https://registry.npmmirror.com"
    if spec.install_cmd.startswith(("uv ", "pip ")) and "UV_INDEX_URL" not in env:
        env["UV_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"

    proc = await asyncio.create_subprocess_shell(
        spec.install_cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=240.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "message": "安装超时（超过 4 分钟），建议在终端手动运行安装命令", "output": "", "item": public_item(spec)}

    out = raw.decode("utf-8", errors="replace").strip()
    pr = probe(spec_id, get_entry(spec_id)["command"])
    ok = proc.returncode == 0 and pr["installed"]
    msg = "安装完成，已检测到命令" if ok else (f"安装结束但仍未检测到命令：{pr['reason']}" if proc.returncode == 0 else f"安装失败（退出码 {proc.returncode}）")
    return {"ok": ok, "message": msg, "output": out[-1200:], "item": public_item(spec)}


def runtime_env(spec_id: str) -> dict[str, str]:
    """构造传给外部 ACP 子进程的环境变量：保留基础环境，按配置注入密钥。"""
    spec = BY_ID[spec_id]
    cfg = get_entry(spec_id)
    env = dict(os.environ)
    extra = os.pathsep.join(str(d) for d in EXTRA_BIN_DIRS if d.exists())
    if extra:
        env["PATH"] = f"{extra}{os.pathsep}{env.get('PATH', '')}"
    if spec.env_key and cfg["api_key"]:
        env[spec.env_key] = cfg["api_key"]
    return env


def to_worker_cards() -> list[WorkerCard]:
    """把已上岗的外部队员转换为 WorkerCard，融入 Yip 现有的队员体系。"""
    cards: list[WorkerCard] = []
    for spec in SPECS:
        if spec.status != "ready":
            continue
        cfg = get_entry(spec.id)
        if not cfg["enabled"]:
            continue
        skills = [
            Skill(
                id=sk["id"],
                name=sk["name"],
                description=sk["description"],
                group=cap["name"],
            )
            for cap in spec.capabilities
            for sk in cap.get("skills") or []
        ]
        cards.append(WorkerCard(
            id=spec.id,
            name=spec.name,
            version="1.0.0",
            introduction=spec.introduction,
            personality=list(spec.personality),
            capabilities=list(spec.capabilities),
            skills=skills,
            inputs=spec.inputs,
            deliverable=spec.deliverable,
            prompt=f"你是 {spec.name}，作为 Yip 的外部队员协作完成当前子任务。",
            source="external",
            enabled=True,
            root="",
            kind="acp",
            team="外部队员",
        ))
    return cards
