"""队员的浏览器：Yip 专用的一个 Chrome（Playwright 驱动，用本机装好的 Chrome，不另下浏览器），登录状态记在
data/browser/ 里，登一次以后都在。平时在后台跑（看不见），右侧浏览器面板通过 CDP Screencast 实时直播画面，
主人可以直接在面板里输网址、点一下、滚动、打字、随时点「停下」接管；极端情况也可弹出可见窗口。

每件事的每个队员一个标签页，主人自己也能开标签页。读页面时给每个能点、能填的东西编号（[3] 按钮「登录」），点和填都按编号来。
自动模式下：打开、看、滚动、点链接都不问；点「提交 / 支付 / 发送 / 删除」这类按钮、往输入框里填东西后提交才问（tools/risk.py）。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import shutil
import time
from pathlib import Path

from .. import db, events
from . import READ, WRITE, Ctx, Tool, ToolError, clip, obj

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_TAB = "user"
SNAP_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; };
  document.querySelectorAll('[data-yip-ref]').forEach(e => e.removeAttribute('data-yip-ref'));
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [contenteditable=true], [onclick]';
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el) || n >= 120) continue;
    n += 1; el.setAttribute('data-yip-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const kind = tag === 'a' ? '链接' : tag === 'button' || el.getAttribute('role') === 'button' ? '按钮'
      : tag === 'select' ? '下拉框' : tag === 'textarea' || tag === 'input' || el.isContentEditable ? '输入框' : (el.getAttribute('role') || tag);
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    const type = tag === 'input' ? (el.getAttribute('type') || 'text') : '';
    const href = tag === 'a' ? (el.getAttribute('href') || '').slice(0, 80) : '';
    out.push(`[${n}] ${kind}${type && type !== 'text' ? '(' + type + ')' : ''}「${label}」${href ? ' → ' + href : ''}`);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 5000);
  return { title: document.title, url: location.href, items: out, text };
}
"""


def _norm_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith(("http://", "https://", "file://", "about:")):
        return u
    return "https://" + u


class Browser:
    """全局一个浏览器、每个队员和主人各自有标签页。headed=True 时是弹出来的可见窗口。"""

    def __init__(self) -> None:
        self.pw = None
        self.ctx = None
        self.headed = False
        self.pages: dict[str, object] = {}
        self.titles: dict[str, str] = {}
        self.active: str | None = None   # 右侧浏览器面板当前显示的标签页
        self.viewport: tuple[int, int] = (1280, 800)
        self.lock = asyncio.Lock()
        self._subs: set[asyncio.Queue] = set()
        self._cdp = None
        self._cdp_key: str | None = None
        self._tab_seq = 0
        self.action: dict | None = None  # {"busy": bool, "label": str, "box": dict|None, "task_id": str|None}
        self._stopped: set[str] = set()  # 主人点「停下」后记录被中断的 task_id

    def profile(self) -> Path:
        p = db.DATA_DIR / "browser"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def broadcast(self, msg: dict) -> None:
        for q in list(self._subs):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(msg)

    def broadcast_state(self) -> None:
        self.broadcast({"type": "state", "data": self.state()})

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subs.add(q)
        await self._sync_screencast()
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
        if not self._subs:
            await self._detach_screencast()

    async def _detach_screencast(self) -> None:
        cdp, self._cdp, self._cdp_key = self._cdp, None, None
        if cdp is not None:
            with contextlib.suppress(Exception):
                await cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                await cdp.detach()

    async def _sync_screencast(self) -> None:
        if not self._subs or self.ctx is None:
            await self._detach_screencast()
            return
        key = self.active
        page = self.pages.get(key or "")
        if page is None or page.is_closed():
            await self._detach_screencast()
            return
        if self._cdp is not None and self._cdp_key == key:
            return
        await self._detach_screencast()
        try:
            cdp = await self.ctx.new_cdp_session(page)
            self._cdp, self._cdp_key = cdp, key

            def on_frame(ev: dict) -> None:
                sid = ev.get("sessionId")
                if sid is not None:
                    asyncio.create_task(self._ack(cdp, sid))
                meta = ev.get("metadata") or {}
                self.broadcast({
                    "type": "frame",
                    "data": ev.get("data", ""),
                    "w": meta.get("deviceWidth") or self.viewport[0],
                    "h": meta.get("deviceHeight") or self.viewport[1],
                })

            cdp.on("Page.screencastFrame", on_frame)
            await cdp.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": 75,
                "maxWidth": self.viewport[0],
                "maxHeight": self.viewport[1],
                "everyNthFrame": 1,
            })
        except Exception:  # noqa: BLE001
            await self._detach_screencast()

    async def _ack(self, cdp, sid: int) -> None:
        with contextlib.suppress(Exception):
            await cdp.send("Page.screencastFrameAck", {"sessionId": sid})

    async def push_frame(self, q: asyncio.Queue | None = None) -> None:
        """静止页面刚连上 WS 时主动补发一帧，避免黑屏等待。"""
        img = await self.shot()
        if not img:
            return
        msg = {"type": "frame", "data": base64.b64encode(img).decode("ascii"),
               "w": self.viewport[0], "h": self.viewport[1]}
        if q is not None:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(msg)
        else:
            self.broadcast(msg)

    def _bind_page_events(self, key: str, page) -> None:
        def on_nav(_frame=None) -> None:
            asyncio.create_task(self._refresh_meta(key, page))

        page.on("framenavigated", on_nav)
        page.on("load", on_nav)
        page.on("close", lambda *_: self._on_page_close(key))

    def _on_page_close(self, key: str) -> None:
        self.pages.pop(key, None)
        self.titles.pop(key, None)
        if self.active == key:
            self.active = next(reversed(self.pages), None)
            asyncio.create_task(self._sync_screencast())
        self.broadcast_state()

    async def _refresh_meta(self, key: str, page) -> None:
        if page.is_closed():
            return
        with contextlib.suppress(Exception):
            t = await page.title()
            if t:
                self.titles[key] = t
        self.broadcast_state()

    async def _launch(self, headed: bool) -> None:
        from playwright.async_api import async_playwright
        if self.pw is None:
            self.pw = await async_playwright().start()
        kw = {"executable_path": CHROME} if os.path.exists(CHROME) else {}
        vw, vh = self.viewport
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        self.ctx = await self.pw.chromium.launch_persistent_context(
            str(self.profile()), headless=not headed, viewport={"width": vw, "height": vh}, locale="zh-CN",
            user_agent=ua,
            ignore_default_args=["--enable-automation"],
            args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled"], **kw)
        await self.ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        self.headed = headed
        self.ctx.on("close", lambda *_: self._closed())

    async def fetch_text(self, url: str, key: str = "fetch") -> str:
        """用本机 Chrome 打开网页并提取渲染后的正文（适合公众号、小红书、JS 渲染页）。"""
        p = await self.page(key, notify_open=True)
        try:
            await p.goto(_norm_url(url), wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"浏览器打不开：{type(e).__name__}") from e
        is_wx = "mp.weixin.qq.com" in (url or "").lower()
        if is_wx:
            with contextlib.suppress(Exception):
                await p.wait_for_selector("#js_content, #activity-name, .rich_media_title, #js_image_desc", timeout=6000)
            await asyncio.sleep(1.2)
        else:
            with contextlib.suppress(Exception):
                await p.wait_for_load_state("networkidle", timeout=5000)
            await asyncio.sleep(0.5)
        await self._refresh_meta(key, p)
        await self.push_frame()
        data = await p.evaluate(r"""() => {
            const title = (document.querySelector('#activity-name, .rich_media_title, h1')?.innerText || document.title || '').trim();
            const author = (document.querySelector('#js_name, .rich_media_meta_nickname')?.innerText || '').trim();
            const contentEl = document.querySelector('#js_content, #js_image_desc, article, main');
            const body = (contentEl ? contentEl.innerText : (document.body ? document.body.innerText : '')).trim();
            const imgs = Array.from(document.querySelectorAll('#js_content img, .swiper-slide img, .share_media_swiper img'))
              .map(i => i.getAttribute('data-src') || i.getAttribute('src') || '')
              .filter(u => u && /^https?:\/\//.test(u)).slice(0, 8);
            return { title, author, body, imgs, url: location.href };
        }""")
        body = (data.get("body") or "").strip()
        title = (data.get("title") or "").strip()
        if is_wx and ("环境异常" in body or "完成验证" in body or "去验证" in body) and len(body) < 300:
            raise ToolError("微信弹出了安全验证，请在右栏「浏览器」里完成验证后再让我重读")
        parts = [data.get("url") or url]
        if title:
            parts.append(f"# {title}")
        if data.get("author"):
            parts.append(f"来源：{data['author']}")
        if body:
            parts.append(body)
        if data.get("imgs"):
            parts.append("配图链接：\n" + "\n".join(data["imgs"]))
            if len(body) < 200:
                parts.append("（提示：这篇主要是图片内容，可用 browser_screenshot 截图看图里的字）")
        out = "\n\n".join(parts).strip()
        if len(body) < 20 and not data.get("imgs"):
            raise ToolError("页面没有读到正文内容")
        return out

    def _closed(self) -> None:
        self.ctx, self.pages, self.titles, self.active = None, {}, {}, None
        self._cdp, self._cdp_key = None, None
        self.action = None
        self.broadcast_state()

    async def ensure(self, headed: bool | None = None) -> None:
        async with self.lock:
            want = self.headed if headed is None else headed
            if self.ctx is not None and want == self.headed:
                return
            urls = {}
            prev_active = self.active
            if self.ctx is not None:  # 换成看得见 / 看不见：记下各标签页在哪，换完再打开
                await self._detach_screencast()
                urls = {k: p.url for k, p in self.pages.items() if not p.is_closed()}
                await self.ctx.close()
                self._closed()
            await self._launch(want)
            for k, u in urls.items():
                page = await self.ctx.new_page()
                self.pages[k] = page
                self._bind_page_events(k, page)
                if u and u != "about:blank":
                    with contextlib.suppress(Exception):
                        await page.goto(u, wait_until="domcontentloaded", timeout=30000)
                        self.titles[k] = await page.title()
            if prev_active and prev_active in self.pages:
                self.active = prev_active
            elif self.pages:
                self.active = next(reversed(self.pages))
            await self._sync_screencast()
            self.broadcast_state()

    async def page(self, key: str, *, notify_open: bool = True):
        await self.ensure()
        p = self.pages.get(key)
        if p is None or p.is_closed():
            stale = [k for k, x in self.pages.items() if x.is_closed()]
            for k in stale:
                self.pages.pop(k, None)
                self.titles.pop(k, None)
            if len(self.pages) >= 6:  # 标签页太多：关掉最早开的
                old = next(iter(self.pages))
                with contextlib.suppress(Exception):
                    await self.pages.pop(old).close()
                self.titles.pop(old, None)
            p = await self.ctx.new_page()
            vw, vh = self.viewport
            with contextlib.suppress(Exception):
                await p.set_viewport_size({"width": vw, "height": vh})
            self.pages[key] = p
            self._bind_page_events(key, p)
        self.active = key
        if notify_open:
            events.publish("browser", {"task_id": key})
        await self._sync_screencast()
        self.broadcast_state()
        if self.headed:
            await p.bring_to_front()
        return p

    async def navigate(self, url: str, key: str | None = None) -> dict:
        target = _norm_url(url)
        if not target:
            return self.state()
        k = key or self.active or USER_TAB
        p = await self.page(k, notify_open=False)
        try:
            await p.goto(target, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"打不开：{type(e).__name__}") from e
        await self._refresh_meta(k, p)
        await self.push_frame()
        return self.state()

    async def new_tab(self, url: str = "") -> dict:
        self._tab_seq += 1
        key = f"user-{self._tab_seq}"
        p = await self.page(key, notify_open=False)
        if url.strip():
            await self.navigate(url, key=key)
        else:
            self.titles[key] = "新标签页"
            self.broadcast_state()
            await self.push_frame()
        return self.state()

    async def switch_tab(self, key: str) -> dict:
        p = self.pages.get(key)
        if p is not None and not p.is_closed():
            self.active = key
            await self._sync_screencast()
            self.broadcast_state()
            await self.push_frame()
        return self.state()

    async def close_tab(self, key: str) -> dict:
        p = self.pages.pop(key, None)
        self.titles.pop(key, None)
        if p is not None and not p.is_closed():
            with contextlib.suppress(Exception):
                await p.close()
        if self.active == key:
            self.active = next(reversed(self.pages), None)
        await self._sync_screencast()
        self.broadcast_state()
        if self.active:
            await self.push_frame()
        return self.state()

    async def nav_action(self, kind: str) -> dict:
        p = self.pages.get(self.active or "")
        if p is None or p.is_closed():
            return self.state()
        with contextlib.suppress(Exception):
            if kind == "back":
                await p.go_back(wait_until="domcontentloaded", timeout=15000)
            elif kind == "forward":
                await p.go_forward(wait_until="domcontentloaded", timeout=15000)
            elif kind == "reload":
                await p.reload(wait_until="domcontentloaded", timeout=20000)
        if self.active:
            await self._refresh_meta(self.active, p)
        await self.push_frame()
        return self.state()

    async def resize(self, w: int, h: int) -> None:
        w = max(400, min(1920, int(w)))
        h = max(300, min(1400, int(h)))
        if (w, h) == self.viewport:
            return
        self.viewport = (w, h)
        p = self.pages.get(self.active or "")
        if p is not None and not p.is_closed():
            with contextlib.suppress(Exception):
                await p.set_viewport_size({"width": w, "height": h})
            await self._detach_screencast()
            await self._sync_screencast()
            await self.push_frame()

    async def mouse(self, kind: str, x: float, y: float, button: str = "left",
                    delta_x: float = 0, delta_y: float = 0) -> None:
        p = self.pages.get(self.active or "")
        if p is None or p.is_closed():
            return
        with contextlib.suppress(Exception):
            if kind == "move":
                await p.mouse.move(x, y)
            elif kind == "down":
                await p.mouse.move(x, y)
                await p.mouse.down(button=button)
            elif kind == "up":
                await p.mouse.move(x, y)
                await p.mouse.up(button=button)
            elif kind == "click":
                await p.mouse.click(x, y, button=button)
            elif kind == "dblclick":
                await p.mouse.dblclick(x, y, button=button)
            elif kind == "wheel":
                await p.mouse.move(x, y)
                await p.mouse.wheel(delta_x, delta_y)

    async def keyboard(self, kind: str, key: str = "", text: str = "") -> None:
        p = self.pages.get(self.active or "")
        if p is None or p.is_closed():
            return
        with contextlib.suppress(Exception):
            if kind == "press" and key:
                await p.keyboard.press(key)
            elif kind == "text" and text:
                await p.keyboard.insert_text(text)

    def set_action(self, label: str, *, box: dict | None = None, busy: bool = True, task_id: str | None = None) -> None:
        self.action = {"busy": busy, "label": label, "box": box, "task_id": task_id}
        self.broadcast({"type": "action", "data": self.action})

    def clear_action(self, task_id: str | None = None) -> None:
        if task_id and self.action and self.action.get("task_id") not in (None, task_id):
            return
        self.action = None
        self.broadcast({"type": "action", "data": None})

    def check_stopped(self, task_id: str) -> None:
        if task_id in self._stopped:
            self._stopped.discard(task_id)
            self.clear_action(task_id)
            raise ToolError("主人点了一下「停下」，已把浏览器交给你操作")

    def stop_ai(self) -> dict:
        """主人点一下「停下」：中止当前正在操作浏览器的 AI 动作并交回控制权。"""
        tid = (self.action or {}).get("task_id") or (self.active if self.active and not self.active.startswith("user") else None)
        if tid:
            self._stopped.add(tid)
        self.action = None
        self.broadcast({"type": "action", "data": None})
        self.broadcast_state()
        return {"ok": True, "stopped": tid}

    async def shot(self) -> bytes | None:
        p = self.pages.get(self.active or "")
        if p is None or p.is_closed():
            return None
        with contextlib.suppress(Exception):
            return await p.screenshot(type="jpeg", quality=72)
        return None

    def state(self) -> dict:
        p = self.pages.get(self.active or "")
        live = p is not None and not p.is_closed()
        t = db.task(self.active) if self.active and not self.active.startswith("user") else None
        tabs = []
        for k, pg in list(self.pages.items()):
            if pg.is_closed():
                continue
            tabs.append({
                "key": k,
                "url": pg.url or "",
                "title": self.titles.get(k) or (pg.url.replace("https://", "").replace("http://", "")[:24] if pg.url and pg.url != "about:blank" else "新标签页"),
                "active": k == self.active,
                "is_ai": not k.startswith("user"),
            })
        return {
            "open": live,
            "url": p.url if live else "",
            "title": self.titles.get(self.active or "", "") if live else "",
            "headed": self.headed,
            "task_id": self.active,
            "who": f"{t['label']} {t['title']}" if t and t.get("label") else "",
            "viewport": {"w": self.viewport[0], "h": self.viewport[1]},
            "tabs": tabs,
            "action": self.action,
        }

    async def close(self) -> None:
        await self._detach_screencast()
        if self.ctx is not None:
            await self.ctx.close()
        if self.pw is not None:
            await self.pw.stop()
        self.ctx, self.pw, self.pages, self.titles, self.active = None, None, {}, {}, None
        self.action = None


B = Browser()


def available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(CHROME) or bool(shutil.which("chromium"))


async def _snap(page, task_id: str | None = None) -> str:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(0.5)
    d = await page.evaluate(SNAP_JS)
    if task_id and d.get("title"):
        B.titles[task_id] = d["title"]
        B.broadcast_state()
    items = "\n".join(d["items"]) or "（这页上没有能点或能填的东西）"
    return clip(f"{d['title']}\n{d['url']}\n\n## 能点、能填的（按编号操作）\n{items}\n\n## 页面文字\n{d['text']}", 12000)


async def _el(page, ref: int | str):
    el = await page.query_selector(f'[data-yip-ref="{int(ref)}"]')
    if el is None:
        raise ToolError(f"找不到编号 {ref}，页面可能变了，先 browser_snapshot 再看一眼")
    return el


async def _box(el) -> dict | None:
    try:
        b = await el.bounding_box()
        if b:
            return {"x": b["x"], "y": b["y"], "w": b["width"], "h": b["height"]}
    except Exception:  # noqa: BLE001
        pass
    return None


async def browser_open(ctx: Ctx, url: str) -> str:
    B.check_stopped(ctx.task_id)
    url = _norm_url(url)
    page = await B.page(ctx.task_id)
    B.set_action(f"正在打开 {url.replace('https://', '').replace('http://', '')[:42]}", task_id=ctx.task_id)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        B.check_stopped(ctx.task_id)
        B.set_action("正在读取页面内容", task_id=ctx.task_id)
        return await _snap(page, ctx.task_id)
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"打不开：{type(e).__name__}") from e
    finally:
        B.clear_action(ctx.task_id)


async def browser_snapshot(ctx: Ctx) -> str:
    B.check_stopped(ctx.task_id)
    page = await B.page(ctx.task_id)
    B.set_action("正在读取页面内容", task_id=ctx.task_id)
    try:
        return await _snap(page, ctx.task_id)
    finally:
        B.clear_action(ctx.task_id)


async def browser_click(ctx: Ctx, ref: int, what: str = "") -> str:
    B.check_stopped(ctx.task_id)
    page = await B.page(ctx.task_id)
    el = await _el(page, ref)
    box = await _box(el)
    B.set_action(f"点一下「{what or ref}」", box=box, task_id=ctx.task_id)
    try:
        await asyncio.sleep(0.25)
        B.check_stopped(ctx.task_id)
        await el.click(timeout=10000)
        return await _snap(page, ctx.task_id)
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"点不了：{type(e).__name__}") from e
    finally:
        B.clear_action(ctx.task_id)


async def browser_type(ctx: Ctx, ref: int, text: str, what: str = "", submit: bool = False) -> str:
    B.check_stopped(ctx.task_id)
    page = await B.page(ctx.task_id)
    el = await _el(page, ref)
    box = await _box(el)
    B.set_action(f"在「{what or ref}」里输入文字", box=box, task_id=ctx.task_id)
    try:
        await asyncio.sleep(0.2)
        B.check_stopped(ctx.task_id)
        await el.fill(text, timeout=10000)
        if submit:
            await el.press("Enter")
        return await _snap(page, ctx.task_id)
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"填不进去：{type(e).__name__}") from e
    finally:
        B.clear_action(ctx.task_id)


async def browser_scroll(ctx: Ctx, direction: str = "down") -> str:
    B.check_stopped(ctx.task_id)
    page = await B.page(ctx.task_id)
    B.set_action("向下翻页" if direction != "up" else "向上翻页", task_id=ctx.task_id)
    try:
        await page.mouse.wheel(0, 700 if direction != "up" else -700)
        return await _snap(page, ctx.task_id)
    finally:
        B.clear_action(ctx.task_id)


async def browser_back(ctx: Ctx) -> str:
    B.check_stopped(ctx.task_id)
    page = await B.page(ctx.task_id)
    B.set_action("返回上一页", task_id=ctx.task_id)
    try:
        await page.go_back(wait_until="domcontentloaded", timeout=20000)
        return await _snap(page, ctx.task_id)
    finally:
        B.clear_action(ctx.task_id)


async def browser_screenshot(ctx: Ctx, path: str = "", full_page: bool = False) -> str:
    """把当前页面截成 PNG 存进项目文件夹，记成产物。"""
    from .. import artifacts, worker
    from .files import inside
    page = await B.page(ctx.task_id)
    if not path:
        folder = (worker._main_of(ctx.task_id).get("title") or "截图").replace("/", "-")
        path = f"{folder}/截图-{time.strftime('%H%M%S')}.png"
    if str(path).startswith(str(ctx.workspace)):
        path = str(path)[len(str(ctx.workspace)):].lstrip("/")
    p = inside(ctx, path if path.lower().endswith((".png", ".jpg", ".jpeg")) else path + ".png")
    p.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(p), full_page=bool(full_page))
    artifacts.record(ctx, p)
    return f"截好了：{p.relative_to(ctx.workspace)}（{p.stat().st_size // 1024} KB），页面是「{await page.title()}」"


async def browser_login(ctx: Ctx, url: str, site: str = "") -> str:
    """要登录才能看：在右侧浏览器面板打开登录页（也可弹出窗口），在「等你确认」里请主人登录，登好了点一下再接着做。"""
    page = await B.page(ctx.task_id)
    try:
        await page.goto(_norm_url(url), wait_until="domcontentloaded", timeout=30000)
    except Exception:  # noqa: BLE001
        pass
    await B.push_frame()
    from .. import worker
    t = db.task(ctx.task_id) or {}
    main = worker._main_of(ctx.task_id)
    name = site or url
    aid = db.insert("approvals", task_id=ctx.task_id, main_id=main.get("id", ctx.task_id), run_id=t.get("run_id"),
                    worker_id=ctx.worker_id, tool="browser_login", args={"url": url, "site": name},
                    summary=f"请在右侧浏览器里登录「{name}」，登好了选「登好了」", risk="external",
                    status="pending", created_at=db.now())
    row = db.q1("SELECT * FROM approvals WHERE id=?", (aid,))
    events.publish("approval", row)
    db.update("tasks", ctx.task_id, note=f"等你登录 {name}")
    events.task_changed(ctx.task_id)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15 * 60:
        await asyncio.sleep(1.5)
        st = (db.q1("SELECT status FROM approvals WHERE id=?", (aid,)) or {}).get("status")
        if st and st != "pending":
            break
    else:
        db.update("approvals", aid, status="cancelled", decided_at=db.now())
        st = "timeout"
    if st == "allowed":
        snap = await _snap(page, ctx.task_id)
        if B.headed:
            await B.ensure(headed=False)
        return "主人登好了。\n" + snap
    return "主人没有登录（或者超时了）。换个不用登录的办法，或者在交付里说明缺了什么。"


TOOLS = [
    Tool("browser_open", "用浏览器打开网页（能跑 JS、能看到登录后的内容），返回页面文字和能点、能填的东西（带编号）。"
         "web_fetch 读不到、要交互、要登录的网站用它", obj({"url": {"type": "string"}}),
         browser_open, READ, lambda a: f"浏览器打开 {str(a.get('url', ''))[:60]}", "浏览器"),
    Tool("browser_snapshot", "重新看一眼当前页面", obj({}, []), browser_snapshot, READ, lambda a: "看页面", "浏览器"),
    Tool("browser_click", "点页面上编号为 ref 的东西；what 写它上面的字（比如「下一页」「提交」）",
         obj({"ref": {"type": "integer"}, "what": {"type": "string"}}, ["ref", "what"]),
         browser_click, WRITE, lambda a: f"点「{a.get('what') or a.get('ref')}」", "点"),
    Tool("browser_type", "往编号为 ref 的输入框里填字；what 写这是什么框；submit=true 填完按回车",
         obj({"ref": {"type": "integer"}, "text": {"type": "string"}, "what": {"type": "string"},
              "submit": {"type": "boolean"}}, ["ref", "text", "what"]),
         browser_type, WRITE, lambda a: f"在「{a.get('what') or a.get('ref')}」里填 {str(a.get('text', ''))[:30]}", "填"),
    Tool("browser_screenshot", "把浏览器当前页面截图存进项目文件夹（PNG），主人要截图时用它；full_page=true 截整页",
         obj({"path": {"type": "string", "description": "相对工作文件夹的路径，比如 百度首页/截图.png；不填自动起名"},
              "full_page": {"type": "boolean"}}, []),
         browser_screenshot, WRITE, lambda a: f"截图 {a.get('path') or ''}".strip(), "截图"),
    Tool("browser_scroll", "上下翻页", obj({"direction": {"type": "string", "enum": ["down", "up"]}}, []),
         browser_scroll, READ, lambda a: "翻页", "浏览器"),
    Tool("browser_back", "后退到上一页", obj({}, []), browser_back, READ, lambda a: "后退", "浏览器"),
    Tool("browser_login", "这个网站要登录才能看：在右侧浏览器打开登录页请主人登录，登好了接着做。site 写网站叫什么",
         obj({"url": {"type": "string"}, "site": {"type": "string"}}, ["url"]),
         browser_login, READ, lambda a: f"请你登录 {a.get('site') or a.get('url')}", "登录"),
]
