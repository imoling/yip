"""联网：web_search 和 web_fetch。

搜索：平时用 Keenable（免 key，Hermes 内置的同一家，每个 IP 每小时 1000 次），搜不动换 DuckDuckGo，
再搜不动或者要挖得深，用 Tavily 连接器（接了才有）。读网页先直接取，取不到或者是前端渲染的空壳，换 Keenable 的抓取。
Keenable 免 key 的请求会记下查询词、IP 和应用名（X-Keenable-Title: iml-yip），不带主人的任何信息。

参照 OpenWorker（MIT）的 coworker/web/tool.py、fetch.py。走系统代理（trust_env），国内外站点都能取。
"""
from __future__ import annotations

import asyncio
import html
import re

import httpx

from .. import sentinel, settings
from . import READ, Ctx, Tool, ToolError, clip, obj

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


def _fmt(rows: list[dict]) -> str:
    if not rows:
        return "没搜到结果，换个关键词再试"
    return "\n\n".join(f"{i}. {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(rows, 1))


async def _ddgs(q: str, n: int) -> list[dict]:
    from ddgs import DDGS

    def go():
        return DDGS(timeout=12).text(q, max_results=n, backend="auto") or []
    try:
        rows = await asyncio.to_thread(go)
    except Exception:  # noqa: BLE001 — 免费搜索偶尔限流，歇一下再试一次
        await asyncio.sleep(2)
        rows = await asyncio.to_thread(go)
    return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in rows]


KEENABLE = "https://api.keenable.ai/v1"
KEENABLE_APP = {"X-Keenable-Title": "iml-yip"}


async def _keenable(q: str, n: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{KEENABLE}/search/public", headers=KEENABLE_APP, json={"query": q, "max_results": n})
        r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""),
             "snippet": (x.get("snippet") or x.get("description") or "")[:800]} for x in r.json().get("results", [])]


async def keenable_fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{KEENABLE}/fetch/public", headers=KEENABLE_APP, params={"url": url, "keenable_title": "iml-yip"})
        r.raise_for_status()
    d = r.json()
    return f"# {d.get('title') or ''}\n\n{d.get('content') or ''}".strip()


async def _tavily(q: str, n: int, key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.tavily.com/search", json={"api_key": key, "query": q, "max_results": n})
        r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])]


async def _bocha(q: str, n: int, key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.bochaai.com/v1/web-search", headers={"Authorization": f"Bearer {key}"},
                         json={"query": q, "count": n, "summary": True})
        r.raise_for_status()
    pages = ((r.json().get("data") or {}).get("webPages") or {}).get("value") or []
    return [{"title": x.get("name", ""), "url": x.get("url", ""), "snippet": x.get("summary") or x.get("snippet", "")}
            for x in pages]


PROVIDERS = {"keenable": "Keenable", "ddgs": "DuckDuckGo", "tavily": "Tavily", "bocha": "博查"}


async def search_with(provider: str, key: str, q: str, n: int) -> list[dict]:
    if provider == "tavily" and key:
        return await _tavily(q, n, key)
    if provider == "bocha" and key:
        return await _bocha(q, n, key)
    if provider == "keenable":
        return await _keenable(q, n)
    return await _ddgs(q, n)


def deep_search() -> tuple[str, str]:
    """深度搜索：接了 Tavily 连接器就用它的 key；老设置里的 deep_provider / deep_key 兜底。"""
    from urllib.parse import parse_qs, urlparse
    from . import mcp
    for cfg in mcp.load().values():
        if cfg.get("preset") != "tavily" or cfg.get("enabled") is False:
            continue
        key = (parse_qs(urlparse(cfg.get("url") or "").query).get("tavilyApiKey") or [""])[0] \
            or (cfg.get("env") or {}).get("TAVILY_API_KEY", "")
        if key:
            return "tavily", key
    s = settings.yip()["search"]
    return s.get("deep_provider") or "", s.get("deep_key") or ""


async def web_search(ctx: Ctx, query: str, max_results: int = 6, deep: bool = False) -> str:
    """日常用 provider；deep=True 或者日常的搜失败了，换深度搜索（Tavily 连接器，接了才有）。"""
    s = settings.yip()["search"]
    n = max(1, min(int(max_results or 6), 10))
    daily = (s.get("provider") or "keenable", s.get("api_key") or "")
    strong = deep_search()
    free = [p for p in [("keenable", ""), ("ddgs", "")] if p[0] != daily[0]]  # 免费的两家互为备份
    has_strong = bool(strong[0] and strong[1])
    order = ([strong] if deep and has_strong else []) + [daily] + free + ([strong] if has_strong and not deep else [])
    last: Exception | None = None
    for prov, key in order:
        try:
            return _fmt(await search_with(prov, key, query, n))
        except Exception as e:  # noqa: BLE001
            last = e
    raise ToolError(f"搜索失败：{type(last).__name__}，可以换关键词重试；常失败的话跟 Yip 说换个搜索服务") from last


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|section|article)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n\n", raw)
    return raw.strip()


NEEDS_BROWSER = re.compile(
    r"^https?://(?:mp\.weixin\.qq\.com|www\.xiaohongshu\.com|xhslink\.com|zhuanlan\.zhihu\.com|www\.zhihu\.com)/",
    re.I,
)


async def _browser_fetch(ctx: Ctx, url: str) -> str | None:
    from . import browser
    if not browser.available():
        return None
    text = await browser.B.fetch_text(url, key=ctx.task_id or "fetch")
    out, _ = sentinel.wrap(clip(text, 12000), source=f"网页 {url[:60]}", task_id=ctx.task_id)
    return out


async def web_fetch(ctx: Ctx, url: str) -> str:
    if not re.match(r"^https?://", url or ""):
        raise ToolError("只支持 http / https 地址")
    if NEEDS_BROWSER.match(url):
        got = await _browser_fetch(ctx, url)
        if got:
            return got
    why = ""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as c:
            r = await c.get(url)
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400:
            why = f"HTTP {r.status_code}"
        elif "html" in ctype or r.text.lstrip().startswith("<"):
            text = html_to_text(r.text)
            if len(text) >= 300:  # 太短多半是前端渲染的空壳，换 Keenable 的抓取
                out, _ = sentinel.wrap(clip(f"{url}\n\n{text}", 12000), source=f"网页 {url[:60]}", task_id=ctx.task_id)
                return out
            why = "页面是空壳"
        elif ctype.startswith("text/") or "json" in ctype:
            out, _ = sentinel.wrap(clip(f"{url}\n\n{r.text}", 12000), source=f"网页 {url[:60]}", task_id=ctx.task_id)
            return out
        else:
            raise ToolError(f"不是网页（{ctype or '未知类型'}），读不了")
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        why = type(e).__name__
    try:
        out, _ = sentinel.wrap(clip(f"{url}\n\n{await keenable_fetch(url)}", 12000), source=f"网页 {url[:60]}", task_id=ctx.task_id)
        return out
    except Exception:  # noqa: BLE001
        pass
    try:
        got = await _browser_fetch(ctx, url)
        if got:
            return got
    except ToolError:
        raise
    except Exception:  # noqa: BLE001
        pass
    raise ToolError(f"打不开：{why}") from None


TOOLS = [
    Tool("web_search", "联网搜索，返回标题、链接和摘要（摘要常带正文片段）。查公开信息时先用它。",
         obj({"query": {"type": "string", "description": "搜索关键词"},
              "max_results": {"type": "integer", "description": "最多几条，默认 6"},
              "deep": {"type": "boolean", "description": "要挖得深、查得准（冷门、最新数据、要出处）时设 true，走深度搜索服务"}},
             ["query"]),
         web_search, READ, lambda a: f"{'深度' if a.get('deep') else ''}搜索「{a.get('query', '')}」", "搜索"),
    Tool("web_fetch", "读取一个网页的正文（纯文本）。搜到链接后要看详情时用它。",
         obj({"url": {"type": "string", "description": "完整网址"}}),
         web_fetch, READ, lambda a: f"读网页 {re.sub(r'^https?://', '', a.get('url', ''))[:60]}", "读网页"),
]
