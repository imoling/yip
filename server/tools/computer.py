"""队员操作电脑（macOS）：看前台应用的界面、按编号点按钮、打字、按快捷键、打开应用。

看界面走「辅助功能」接口（AXUIElement），拿到的是按钮、输入框的名字和位置，不靠截图猜坐标，准而且不用看图模型。
点优先用 AXPress，按不动再在元素中心模拟一次鼠标点击；打字和快捷键用 Quartz 键盘事件发给前台应用。

要在 系统设置 → 隐私与安全性 → 辅助功能 里允许 Yip（开发时是跑后端的终端或 Python）。
每一步点、打字、按键、打开应用都先问主人（risk.ALWAYS_ASK），不受模式和「本次都允许」影响。
"""
from __future__ import annotations

import asyncio
import subprocess
import sys

from . import EXEC, READ, Ctx, Tool, ToolError, clip, obj

MAX_ITEMS = 150
ROLE = {"AXButton": "按钮", "AXTextField": "输入框", "AXTextArea": "输入框", "AXSearchField": "搜索框", "AXCheckBox": "勾选框",
        "AXRadioButton": "单选", "AXPopUpButton": "下拉框", "AXComboBox": "下拉框", "AXMenuItem": "菜单项",
        "AXMenuBarItem": "菜单", "AXLink": "链接", "AXTab": "标签页", "AXRow": "行", "AXCell": "格子", "AXStaticText": "文字",
        "AXImage": "图片", "AXSlider": "滑块", "AXMenuButton": "菜单按钮", "AXDisclosureTriangle": "展开"}
ACTIONABLE = {"AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXCheckBox", "AXRadioButton", "AXPopUpButton",
              "AXComboBox", "AXMenuItem", "AXLink", "AXTab", "AXRow", "AXMenuButton", "AXDisclosureTriangle", "AXCell",
              "AXSlider"}
KEYCODE = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14,
           "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27,
           "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, ";": 41,
           ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
           "backspace": 51, "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126}
# 中文名打不开（open -a 认英文名）：常用的系统应用对一下
APP_EN = {"计算器": "Calculator", "备忘录": "Notes", "日历": "Calendar", "提醒事项": "Reminders", "访达": "Finder",
          "邮件": "Mail", "信息": "Messages", "照片": "Photos", "音乐": "Music", "地图": "Maps", "通讯录": "Contacts",
          "系统设置": "System Settings", "预览": "Preview", "文本编辑": "TextEdit", "终端": "Terminal", "天气": "Weather",
          "股市": "Stocks", "播客": "Podcasts", "图书": "Books", "家庭": "Home", "快捷指令": "Shortcuts", "时钟": "Clock"}
_REFS: dict[str, dict[int, object]] = {}   # 每个队员最近一次看到的界面：编号 → 元素


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import ApplicationServices  # noqa: F401
        import Quartz  # noqa: F401
    except ImportError:
        return False
    return True


def trusted() -> bool:
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())


def _need() -> None:
    if not available():
        raise ToolError("这台电脑上用不了操作电脑的功能")
    if not trusted():
        raise ToolError("还没有「辅助功能」权限：请主人在 系统设置 → 隐私与安全性 → 辅助功能 里允许 Yip，允许后再试")


def _attr(el, name):
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, v = AXUIElementCopyAttributeValue(el, name, None)
    return v if err == 0 else None


def _xy(el) -> tuple[float, float, float, float] | None:
    from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
    p, s = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if p is None or s is None:
        return None
    ok1, pt = AXValueGetValue(p, kAXValueCGPointType, None)
    ok2, sz = AXValueGetValue(s, kAXValueCGSizeType, None)
    if not (ok1 and ok2):
        return None
    return pt.x, pt.y, sz.width, sz.height


def parse_keys(keys: str) -> tuple[int, int]:
    """「cmd+shift+n」→（键码，修饰键掩码）。"""
    import Quartz
    mask = {"cmd": Quartz.kCGEventFlagMaskCommand, "command": Quartz.kCGEventFlagMaskCommand,
            "shift": Quartz.kCGEventFlagMaskShift, "alt": Quartz.kCGEventFlagMaskAlternate,
            "option": Quartz.kCGEventFlagMaskAlternate, "ctrl": Quartz.kCGEventFlagMaskControl,
            "control": Quartz.kCGEventFlagMaskControl}
    parts = [p.strip().lower() for p in keys.replace("＋", "+").split("+") if p.strip()]
    if not parts or parts[-1] not in KEYCODE:
        raise ToolError(f"不认识这个按键：{keys}")
    flags = 0
    for p in parts[:-1]:
        if p not in mask:
            raise ToolError(f"不认识这个修饰键：{p}")
        flags |= mask[p]
    return KEYCODE[parts[-1]], flags


def _front():
    from AppKit import NSWorkspace
    return NSWorkspace.sharedWorkspace().frontmostApplication()


def _look(task_id: str, app_name: str = "") -> str:
    from AppKit import NSWorkspace
    from ApplicationServices import AXUIElementCreateApplication
    apps = NSWorkspace.sharedWorkspace().runningApplications()
    app = next((a for a in apps if app_name and (a.localizedName() or "") == app_name), None) or _front()
    root = AXUIElementCreateApplication(app.processIdentifier())
    wins = _attr(root, "AXWindows") or []
    lines, refs, n = [], {}, 0
    stack = [(w, 0) for w in reversed(list(wins)[:2])]
    seen = 0
    while stack and n < MAX_ITEMS and seen < 4000:
        el, depth = stack.pop()
        seen += 1
        role = _attr(el, "AXRole") or ""
        if role in ACTIONABLE or role == "AXStaticText":  # 显示屏、结果这类文字常在很深的层级里
            label = next((str(v) for v in (_attr(el, "AXTitle"), _attr(el, "AXDescription"), _attr(el, "AXValue"),
                                            _attr(el, "AXPlaceholderValue"), _attr(el, "AXHelp")) if v), "").strip()
            label = " ".join(label.split())[:60]
            if role == "AXStaticText":
                if label:
                    lines.append(f"    {label}")
            elif label or role in ("AXTextField", "AXTextArea", "AXSearchField"):
                n += 1
                refs[n] = el
                lines.append(f"[{n}] {ROLE.get(role, role.removeprefix('AX'))}「{label}」")
        kids = _attr(el, "AXChildren") or []
        for k in reversed(list(kids)[:80]):
            stack.append((k, depth + 1))
    _REFS[task_id] = refs
    title = next((str(_attr(w, "AXTitle")) for w in list(wins)[:1] if _attr(w, "AXTitle")), "")
    head = f"前台应用：{app.localizedName()}" + (f"，窗口「{title}」" if title else "")
    return clip(head + "\n\n## 能点、能填的（按编号操作）\n" + ("\n".join(lines) or "（没读到界面元素）"), 12000)


def _el(task_id: str, ref: int):
    el = (_REFS.get(task_id) or {}).get(int(ref))
    if el is None:
        raise ToolError(f"找不到编号 {ref}，先 computer_look 再看一眼界面")
    return el


def _click(task_id: str, ref: int) -> None:
    import Quartz
    from ApplicationServices import AXUIElementPerformAction
    el = _el(task_id, ref)
    if AXUIElementPerformAction(el, "AXPress") == 0:
        return
    box = _xy(el)
    if not box:
        raise ToolError("这个东西点不了")
    x, y = box[0] + box[2] / 2, box[1] + box[3] / 2
    for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _type(text: str) -> None:
    import time
    import Quartz
    for ch in text:
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.005)


def _key(keys: str) -> None:
    import Quartz
    code, flags = parse_keys(keys)
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


async def computer_look(ctx: Ctx, app: str = "") -> str:
    _need()
    return await asyncio.to_thread(_look, ctx.task_id, app)


async def computer_open(ctx: Ctx, app: str) -> str:
    _need()
    name = APP_EN.get(app.strip(), app.strip())
    r = await asyncio.to_thread(subprocess.run, ["open", "-a", name], capture_output=True, text=True, timeout=20)
    if r.returncode:
        raise ToolError(f"打不开「{app}」：{(r.stderr or '').strip()[:120] or '没装这个应用'}")
    await asyncio.sleep(1.5)
    return await asyncio.to_thread(_look, ctx.task_id, "")


async def computer_click(ctx: Ctx, ref: int, what: str = "") -> str:
    _need()
    await asyncio.to_thread(_click, ctx.task_id, ref)
    await asyncio.sleep(0.8)
    return await asyncio.to_thread(_look, ctx.task_id, "")


async def computer_type(ctx: Ctx, text: str, ref: int | None = None, what: str = "") -> str:
    _need()
    if ref is not None:
        await asyncio.to_thread(_click, ctx.task_id, ref)
        await asyncio.sleep(0.3)
    await asyncio.to_thread(_type, text)
    await asyncio.sleep(0.4)
    return await asyncio.to_thread(_look, ctx.task_id, "")


async def computer_key(ctx: Ctx, keys: str) -> str:
    _need()
    parse_keys(keys)
    await asyncio.to_thread(_key, keys)
    await asyncio.sleep(0.6)
    return await asyncio.to_thread(_look, ctx.task_id, "")


TOOLS = [
    Tool("computer_look", "看电脑上前台应用（或指定应用）的界面：按钮、输入框、文字，带编号", obj({"app": {"type": "string"}}, []),
         computer_look, READ, lambda a: f"看 {a.get('app') or '前台应用'} 的界面", "看电脑"),
    Tool("computer_open", "打开或切到一个 Mac 应用（比如 备忘录、日历、Finder）", obj({"app": {"type": "string"}}),
         computer_open, EXEC, lambda a: f"打开「{a.get('app')}」", "打开应用"),
    Tool("computer_click", "点界面上编号为 ref 的东西；what 写它上面的字", obj({"ref": {"type": "integer"}, "what": {"type": "string"}},
                                                                     ["ref", "what"]),
         computer_click, EXEC, lambda a: f"在电脑上点「{a.get('what') or a.get('ref')}」", "点"),
    Tool("computer_type", "在电脑上打字；给了 ref 会先点那个输入框", obj({"text": {"type": "string"}, "ref": {"type": "integer"},
                                                                 "what": {"type": "string"}}, ["text"]),
         computer_type, EXEC, lambda a: f"在电脑上打字：{str(a.get('text', ''))[:40]}", "打字"),
    Tool("computer_key", "按快捷键或特殊键，比如 cmd+n、return、esc、cmd+s", obj({"keys": {"type": "string"}}),
         computer_key, EXEC, lambda a: f"按 {a.get('keys')}", "按键"),
]
