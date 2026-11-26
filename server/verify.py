"""验收：队员说做完了，先查一遍再算完成。

两层：
1. 机器检查——文件在不在、网页引用的本地文件在不在、网页打开有没有报错（有 Chrome / Edge 时用无头模式跑一遍）、
   js 和 py 有没有语法错。这些不靠模型，查出来就是硬问题。
2. 模型评审——拿这块活的验收标准、队员的交代和文件内容，让主模型判过不过、差在哪。

不过就把问题列表还给队员返工（worker.py 里最多返工 MAX_REWORK 次）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from . import db, llm

MAX_REWORK = 2


def today() -> str:
    """评审、交付检查、队员都要知道今天几号：不然「明天开会」的日期会被判错。"""
    import datetime as dt
    return dt.datetime.now().strftime("%Y-%m-%d %A")
BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
TEXT = {".md", ".txt", ".html", ".htm", ".js", ".mjs", ".css", ".py", ".json", ".csv", ".ts", ".sh", ".yaml", ".yml",
        ".eml", ".svg", ".xml"}
DSML = re.compile(r"<[｜|]+\s*DSML")


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("src", "href"):
            v = (a.get(k) or "").strip()
            if v and tag in ("script", "link", "img", "audio", "source", "video") \
                    and not re.match(r"^(?:[a-z]+:|//|#|data:)", v, re.I):
                self.refs.append(v.split("?")[0].split("#")[0])


def browser() -> str | None:
    env = os.environ.get("SA_BROWSER")
    if env:
        return env if Path(env).exists() else None
    return next((b for b in BROWSERS if Path(b).exists()), None)


async def _run(cmd: list[str], timeout: float = 25) -> tuple[int, str, str]:
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return -1, "", "超时"
    return p.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


PROBE = """<script>
window.addEventListener("load", () => setTimeout(async () => {{
  try {{ const r = await (async () => {{ {script} }})(); if (r !== undefined) console.log("__YIP__ " + JSON.stringify(r)); }}
  catch (e) {{ console.error("测试脚本出错：" + e); }}
}}, 300));
</script>"""


async def run_page(path: Path, script: str = "", wait_ms: int = 3000) -> dict:
    """用无头浏览器打开网页：返回报错、console 输出、脚本结果和页面文字。没有浏览器时 ok=False。
    script 是一段 JS（可以 await、可以 return），在页面加载后跑，用来模拟按键、点按钮、读状态。"""
    b = browser()
    if not b:
        return {"ok": False, "errors": [], "logs": [], "result": None, "text": ""}
    target, tmp = path, None
    if script.strip():  # 在同一个文件夹里放一份带测试脚本的副本，相对路径引用照样能找到
        html = path.read_text("utf-8", errors="replace")
        probe = PROBE.format(script=script)
        html = html.replace("</body>", probe + "</body>") if "</body>" in html else html + probe
        tmp = path.parent / f".yip-check-{os.getpid()}-{id(script)}.html"
        tmp.write_text(html, "utf-8")
        target = tmp
    prof = Path(tempfile.mkdtemp(prefix="yip-verify-", dir=_cache()))
    try:
        code, dom, err = await _run([b, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                     f"--user-data-dir={prof}", "--enable-logging=stderr", "--v=0",
                                     f"--virtual-time-budget={wait_ms}", "--dump-dom", target.as_uri()])
    finally:
        shutil.rmtree(prof, ignore_errors=True)
        if tmp:
            tmp.unlink(missing_ok=True)
    errors, logs, result = [], [], None
    for ln in err.splitlines():
        m = re.search(r"CONSOLE[^\]]*\]\s*\"(.+?)\", source: (\S+) \((\d+)\)", ln)
        if not m:
            continue
        msg = m.group(1)
        if msg.startswith("__YIP__ "):
            try:
                result = json.loads(msg[8:])
            except json.JSONDecodeError:
                result = msg[8:]
        elif re.search(r"Uncaught|Error|error|Failed|失败|出错", msg):
            errors.append(f"第 {m.group(3)} 行：{msg[:200]}")
        else:
            logs.append(msg[:200])
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", dom)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return {"ok": code == 0, "errors": errors, "logs": logs[:30], "result": result, "text": text[:3000],
            "canvas": "<canvas" in dom}


async def check_html(path: Path) -> list[str]:
    probs = []
    parser = _Refs()
    try:
        parser.feed(path.read_text("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        pass
    for ref in parser.refs:
        if not (path.parent / ref).exists():
            probs.append(f"{path.name} 引用的 {ref} 不存在")
    r = await run_page(path)
    probs += [f"{path.name} 打开时报错（{e}）" for e in r["errors"]]
    if r["ok"] and not r["text"] and not r["canvas"]:
        probs.append(f"{path.name} 打开后页面是空的")
    return probs


async def check_js(path: Path) -> list[str]:
    node = shutil.which("node")
    if not node or path.suffix not in (".js", ".mjs"):
        return []
    code, _, err = await _run([node, "--check", str(path)], 15)
    return [f"{path.name} 语法错：{err.strip().splitlines()[-1][:200] if err.strip() else '未知'}"] if code else []


async def check_py(path: Path) -> list[str]:
    py = shutil.which("python3")
    if not py or getattr(sys, "frozen", False):
        return []
    code, _, err = await _run([py, "-m", "py_compile", str(path)], 15)
    return [f"{path.name} 语法错：{err.strip().splitlines()[-1][:200]}"] if code else []


def _cache() -> str:
    d = Path.home() / "Library" / "Caches" / "cn.imoling.yip"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def files_of(task_id: str) -> list[Path]:
    return [Path(a["path"]) for a in db.q("SELECT path FROM artifacts WHERE task_id=? ORDER BY created_at", (task_id,))]


FORMATS = [(r"(?i)(?<![a-z_])word(?![a-z_.\-]|\s*count|\s*cloud|\s*freq)|docx|\.doc\b|Word 文档", ".docx", "Word"), (r"(?i)excel|xlsx|电子表格", ".xlsx", "Excel"),
           (r"(?i)pptx|\bppt\b|幻灯片|演示文稿", ".pptx", "PPT"), (r"(?i)\bpdf\b", ".pdf", "PDF")]


def wanted_formats(goal: str) -> list[tuple[str, str]]:
    """这块活点名要的文件格式，返回（后缀，叫法）的列表。"""
    return [(suf, name) for pat, suf, name in FORMATS if re.search(pat, goal or "")]


def format_gaps(goal: str, paths: list[Path]) -> list[str]:
    have = {p.suffix.lower() for p in paths if p.exists()}
    return [f"要的是 {name} 文件，没交 {suf}（用 python-docx / openpyxl / python-pptx 生成真文件）"
            for suf, name in wanted_formats(goal) if suf not in have]


async def check_office(path: Path) -> list[str]:
    from . import docview, uploads
    try:
        if path.suffix.lower() in (".docx", ".xlsx", ".pptx"):
            docview.render(path)
        if not uploads.extract(path).strip():
            return [f"{path.name} 打开后没有内容"]
    except Exception as e:  # noqa: BLE001
        return [f"{path.name} 打不开（{type(e).__name__}），文件可能是坏的"]
    return []


FIXTURE = re.compile(r"(?i)空|empty|blank|样例|示例|sample|example|test|测试|fixture|mock")


IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def image_facts(p: Path) -> dict:
    """图片的机器事实：尺寸、是不是一片空白。打不开返回 error。"""
    try:
        from PIL import Image, ImageStat
        with Image.open(p) as im:
            w, h = im.size
            g = im.convert("L")
            g.thumbnail((256, 256))
            std = ImageStat.Stat(g).stddev[0]
        return {"w": w, "h": h, "blank": std < 3}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


async def describe_images(paths: list[Path]) -> dict[str, str]:
    """让能看图的模型把交付的图片说一遍，给评审核对内容用；看不了就空着（尺寸和空白由机器检查管）。"""
    from . import uploads
    out = {}
    for p in [x for x in paths if x.exists() and x.suffix.lower() in IMAGES][:3]:
        u = {"path": str(p), "mime": "image/png" if p.suffix.lower() == ".png" else "image/jpeg"}
        try:
            out[p.name] = (await uploads.describe([u], "这张图是什么页面或内容？用两三句话说清")) or ""
        except Exception:  # noqa: BLE001
            out[p.name] = ""
    return out


def _scratch(name: str, reply: str) -> bool:
    """测试、调试用的临时文件：以 _ 或 . 开头，或者交代里提到它的那句话说了删掉。"""
    if re.match(r"^[._]", name):
        return True
    if FIXTURE.search(Path(name).stem) and Path(name).suffix.lower() in (".txt", ".csv", ".json", ".tsv"):
        return True  # 测试用的样例数据（测试.txt、样例_空文件.txt），不是交付物
    said = next((c for c in re.split(r"[。；;\n]", reply or "") if name in c), "")
    return bool(re.search(r"删|清理|临时|移除", said))


async def machine(paths: list[Path], reply: str) -> list[str]:
    probs = []
    if DSML.search(reply or ""):
        probs.append("最后的回复里混着没执行的工具调用标记")
    for p in paths:
        if not p.exists():
            if p.name in (reply or "") and not _scratch(p.name, reply):  # 交代里还当交付物提到才算；临时文件不算
                probs.append(f"{p.name} 说写了但文件不存在")
            continue
        suf = p.suffix.lower()
        if suf in TEXT and p.stat().st_size == 0:
            if not FIXTURE.search(p.stem):  # 测试故意造的空文件（样例_空文件.txt）不算问题
                probs.append(f"{p.name} 是空文件")
            continue
        if suf in IMAGES:
            f = image_facts(p)
            if f.get("error"):
                probs.append(f"{p.name} 打不开，图片可能是坏的")
            elif f.get("blank"):
                probs.append(f"{p.name} 是一片空白")
            continue
        if suf in (".html", ".htm"):
            probs += await check_html(p)
        elif suf in (".js", ".mjs"):
            probs += await check_js(p)
        elif suf == ".py":
            probs += await check_py(p)
        elif suf in (".docx", ".xlsx", ".pptx", ".pdf"):
            probs += await check_office(p)
        elif suf in TEXT and DSML.search(p.read_text("utf-8", errors="replace")[:20000]):
            probs.append(f"{p.name} 里混着工具调用标记，内容没写对")
    return probs


def _excerpts(paths: list[Path], budget: int = 40000, seen: dict[str, str] | None = None) -> str:
    """把文件贴给评审。贴不全时写明「没贴完」，免得评审把截断当成文件不完整。图片给尺寸和看图模型的描述。"""
    out, left = [], budget
    for p in paths:
        if p.exists() and p.suffix.lower() in IMAGES:
            f = image_facts(p)
            desc = (seen or {}).get(p.name)
            out.append(f"### {p.name}（图片，机器检查：{f.get('w')}×{f.get('h')}，{'空白' if f.get('blank') else '有内容，不是空白'}）"
                       + (f"\n看图模型说：{desc}" if desc else "\n（内容没让模型看，按机器检查和队员交代核对）"))
            continue
        office = p.suffix.lower() in (".docx", ".xlsx", ".xlsm", ".pptx", ".pdf")
        if not p.exists() or (p.suffix.lower() not in TEXT and not office):
            out.append(f"### {p.name}（{p.suffix} 文件，内容不贴）")
            continue
        if office:  # Word / Excel / PDF 抽出文字给评审看
            from . import uploads
            t = uploads.extract(p) or "（抽不出文字）"
        else:
            t = p.read_text("utf-8", errors="replace")
        if left <= 0:
            out.append(f"### {p.name}（共 {len(t)} 字，篇幅不够没贴；是否完整以机器检查为准）")
            continue
        cut = t[:min(left, 24000)]
        left -= len(cut)
        tail = "" if len(cut) == len(t) else \
            f"\n（文件共 {len(t)} 字，这里只贴了前 {len(cut)} 字；后面是没贴出来，不是文件缺了，是否完整以机器检查为准）"
        out.append(f"### {p.name}\n```\n{cut}\n```{tail}")
    return "\n\n".join(out)


JUDGE = """你是验收员。队员说这块活做完了，你按验收标准逐条核对，只看交付物本身，不听队员自夸。今天是 {today}。
- 每条验收标准都要有证据（文件内容、检查结果）支持才算过
- 机器检查查出的问题一律算不过；机器检查没报错就说明文件能完整加载，不要因为贴出来的内容被截断就判文件不完整
- 代码类：看有没有明显跑不起来、功能缺失、初始状态不对（比如一打开就结束）、交互没接上的地方
- 文字类：看有没有答非所问、编数字、空洞没内容、漏了要求
- 图片的尺寸、是不是空白以机器检查为准；你看不到图片本身不是不过的理由，有看图模型的描述就按描述核对内容
- 文件放在项目文件夹的哪一层不卡，主人点名要求放哪里时才算
- 小瑕疵不卡，影响使用或不符合要求的才算问题
只输出 JSON：{{"pass": true/false, "problems": ["具体问题，一句一条，说清在哪个文件哪里"], "score": 0 到 10}}

## 整件事
{main_goal}
## 这块活
{goal}
## 验收标准
{accept}
## 必须遵守的要求
{cons}
## 队员的交代
{reply}
## 机器检查
{machine}
## 交付的文件
{files}"""


async def judge(task: dict, reply: str, paths: list[Path], mach: list[str]) -> dict:
    main = db.task(task["parent_id"]) if task.get("parent_id") else task
    accept = task.get("accept") or []
    r = await llm.chat_json([{"role": "user", "content": JUDGE.format(
        today=today(), main_goal=(main or {}).get("goal", ""), goal=task["goal"],
        accept="\n".join(f"- {a}" for a in accept) or "- 做到了这块活要求的事，交付物能直接用",
        cons="\n".join(f"- {c}" for c in task.get("constraints") or []) or "无",
        reply=(reply or "")[:3000], machine="\n".join(f"- {m}" for m in mach) or "没发现问题（查过文件在不在、网页能否完整加载且无报错、脚本语法）",
        files=_excerpts(paths, seen=await describe_images(paths)) or "（没有交文件）")}], timeout=120)
    probs = [str(x) for x in r.get("problems") or [] if str(x).strip()][:8]
    ok = bool(r.get("pass")) and not mach
    return {"pass": ok, "problems": probs, "score": r.get("score")}


SOFTWARE = re.compile(r"(?i)网页|网站|游戏|小程序|程序|脚本|代码|开发|app\b|html|python|javascript|插件|接口|爬虫|工具")
STRICT_WORKERS = {"developer", "tester", "code-reviewer"}
CODE_FILES = {".html", ".htm", ".js", ".mjs", ".ts", ".py", ".sh", ".css"}


def strict(task_id: str) -> bool:
    """交付前就要模型评审的活：写软件、做网页、拆活标了「难」、开发 / 测试 / 审查队员的活、交了代码文件的。
    其余的先交给主人，评审在后台跑（graph.post_review）。"""
    t = db.task(task_id) or {}
    main = db.task(t["parent_id"]) if t.get("parent_id") else t
    if t.get("hard") or t.get("worker_id") in STRICT_WORKERS:
        return True
    if SOFTWARE.search(f"{(main or {}).get('goal', '')} {t.get('goal', '')}"):
        return True
    return any(p.suffix.lower() in CODE_FILES for p in files_of(task_id))


async def review(task_id: str, reply: str, *, model: bool = True) -> dict:
    """返回 {"pass": bool, "problems": [...], "machine": [...], "score": n|None}。model=False 只做机器检查（几秒）。"""
    task = db.task(task_id)
    paths = files_of(task_id)
    job = [Path(a["path"]) for a in db.q("SELECT path FROM artifacts WHERE main_id=?", (task.get("parent_id") or task_id,))]
    # 点名的格式只查最后一步：中间写稿的人不用交 .pptx（上次写内容稿的被判「没交 PPT」退回了）
    later = task.get("parent_id") and any(task_id in (k.get("prereqs") or []) for k in db.children(task["parent_id"]))
    mach = await machine(paths, reply) + ([] if later else format_gaps(task["goal"], paths + job))
    if not llm.configured() or not model:
        return {"pass": not mach, "problems": mach, "machine": mach, "score": None}
    try:
        j = await judge(task, reply, paths, mach)
    except Exception as e:  # noqa: BLE001 — 评审本身出错不卡交付，只看机器检查
        return {"pass": not mach, "problems": mach, "machine": mach, "score": None, "error": type(e).__name__}
    probs = mach + [p for p in j["problems"] if p not in mach]
    return {"pass": j["pass"] and not mach, "problems": probs, "machine": mach, "score": j["score"]}


def feedback(problems: list[str], n: int) -> str:
    return (f"验收没过（第 {n} 次返工）。问题：\n" + "\n".join(f"- {p}" for p in problems)
            + "\n逐条改掉，改完自己再检查一遍；最后一条回复照旧说清做了什么、交了哪些文件。")
