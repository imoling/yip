"""放行规则：工具的风险级别 × 输入框里选的模式 × 这件事里已经给过的「都允许」。

模式（设置里的 approval）：
- auto（默认）：工作文件夹里读、写、跑命令都不问；删东西、碰工作文件夹以外、装软件、联网下载脚本、
  调连接器往外写数据才问
- plan：动手的规矩同 auto，另外拆完活先给主人看，说开工才派（graph.confirm）
- ask_writes：只读放行，写文件、跑命令、调连接器都先问
- ask_all：每一步都问（老设置，界面上不再给）
- allow_all：除了发邮件，什么都不问
发邮件不管哪种模式、不管「本次都允许」，每次都问（主人定的）。

参照 OpenWorker（MIT）的 coworker/risk.py 和 permissions.py。
"""
from __future__ import annotations

import re

from . import EXEC, EXTERNAL, READ, WRITE

ALWAYS_FREE = {"load_skill", "team_post", "send_back", "browser_login"}   # 读技能说明、传话、请主人登录（自己会问）：不再另问
ALWAYS_ASK = {"mail_send", "ask_user"}  # 发邮件、向主人出选项卡片：每次都问，不给「都允许」
# 操作电脑：任何模式都问（全部放行也问），但主人可以选「这件事里的电脑操作都允许」，同一件事后面不再问
COMPUTER = {"computer_open", "computer_click", "computer_type", "computer_key"}
COMPUTER_GRANT = "computer_*"
POLICIES = ("auto", "plan", "ask_writes", "ask_all", "allow_all")

# 自动模式下跑命令也要问的：删东西、提权、装软件、联网拉脚本来跑、推代码、关机杀进程
RISKY_CMD = re.compile(
    r"(?:^|[\s;&|(])(?:rm|rmdir|unlink|shred|sudo|su|chmod|chown|kill|killall|pkill|shutdown|reboot|halt|dd|mkfs|diskutil|launchctl"
    r"|osascript|automator|shortcuts)\b"  # AppleScript、快捷指令能操控任何应用（包括让「邮件」发信），和操作电脑一样要问
    r"|\b(?:pip3?|npm|pnpm|yarn|brew|apt(?:-get)?|gem|cargo|go|uv)\s+(?:install|add|i|get|tool\s+install)\b"
    r"|\b(?:curl|wget)\b[^|\n]*\|\s*(?:ba|z)?sh\b"
    r"|\bgit\s+(?:push|reset\s+--hard|clean)\b"
    r"|\bfind\b[^\n]*\s-delete\b")
# 浏览器里点这些、填完提交，算「对外做了事」：自动模式也要问
SUBMIT = re.compile(r"(?i)提交|确认|确定|支付|付款|购买|下单|结算|发送|发布|发表|删除|注销|转账|充值|订阅|报名|预约|保存|"
                    r"submit|confirm|pay|buy|purchase|order|checkout|send|publish|post|delete|remove|save|subscribe")
SECRET_FIELD = re.compile(r"(?i)密码|口令|验证码|password|passwd|otp|验证")
READ_ONLY = re.compile(r"^(?:[a-z0-9]+[_-])*(?:get|list|search|read|query|fetch|find|describe|show|lookup|view|status)",
                       re.I)


def outside(command: str, workspace: str) -> bool:
    """命令往工作文件夹以外的地方写：重定向、cp / mv / mkdir / touch 到外面的绝对路径或 ~。"""
    ws = workspace.rstrip("/")
    targets = re.findall(r">>?\s*['\"]?([~/][^\s'\";|&]*)", command)
    for m in re.finditer(r"\b(cp|mv|mkdir|touch|ln|tee|rsync)\b([^;|&\n]*)", command):
        paths = re.findall(r"(?:^|\s)['\"]?([~/][^\s'\"]*)", m.group(2))
        if m.group(1) in ("cp", "ln", "rsync"):  # 从技能目录拷模板进来不算往外写：只看最后的目标
            last = m.group(2).split()[-1:] if m.group(2).split() else []
            paths = [x for x in paths if last and last[0].strip("'\"") == x]
        targets += paths
    for t in targets:
        if t.startswith("/dev/null"):
            continue
        if t.startswith("~") or not (t == ws or t.startswith(ws + "/")):
            return True
    return False


def risky(tool: str, risk: str, args: dict | None, workspace: str = "") -> bool:
    """自动模式下这一步要不要问。"""
    args = args or {}
    if risk == READ:
        return False
    if risk == EXEC:
        cmd = str(args.get("command") or "")
        return bool(RISKY_CMD.search(cmd)) or (bool(workspace) and outside(cmd, workspace))
    if tool in ("browser_click", "browser_type"):  # 浏览器：点链接、填搜索框不问；提交、付款、填密码才问
        what = f"{args.get('what') or ''} {args.get('text') or ''}"
        return bool(SUBMIT.search(str(args.get("what") or ""))) or bool(SECRET_FIELD.search(what)) \
            or (tool == "browser_type" and bool(args.get("submit")) and not re.search(r"搜索|search|查询", what, re.I))
    if risk == EXTERNAL:  # 连接器：查东西不问，往外写才问
        return not READ_ONLY.match(tool.split("__")[-1])
    return False  # 写文件：工具本身只许写工作文件夹


def decide(tool: str, risk: str, policy: str, grants: list[str] | None = None, args: dict | None = None,
           workspace: str = "") -> str:
    """返回 allow 或 ask。grants：这件事里点过「本次都允许」的工具名，"*" 表示全部。"""
    grants = grants or []
    if tool in ALWAYS_ASK:
        return "ask"
    if tool in COMPUTER:
        return "allow" if COMPUTER_GRANT in grants or "*" in grants else "ask"
    if tool in ALWAYS_FREE or policy == "allow_all":
        return "allow"
    if "*" in grants or tool in grants:
        return "allow"
    if policy == "ask_all":
        return "ask"
    if policy in ("auto", "plan"):
        return "ask" if risky(tool, risk, args, workspace) else "allow"
    return "allow" if risk == READ else "ask"


RISK_CN = {READ: "只读", WRITE: "写文件", EXEC: "执行命令", EXTERNAL: "连接器"}
