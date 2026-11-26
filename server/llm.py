"""OpenAI 兼容接口客户端。未配置时调用方自己走兜底。"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

import httpx

from . import settings


class LLMError(Exception):
    pass


STATUS = {"ok": None, "error": "", "at": 0}


def configured() -> bool:
    return settings.configured()


def status() -> dict:
    if not configured():
        return {"state": "off", "text": "未配置模型"}
    if STATUS["ok"] is False:
        return {"state": "error", "text": f"模型连不上：{human_reason(STATUS['error'])}", "detail": STATUS["error"]}
    return {"state": "ok", "text": "模型已连接"}


def _mark(ok: bool, err: str = ""):
    STATUS.update(ok=ok, error=err[:300], at=int(time.time()))


def _target(fast: bool, override: dict | None) -> dict:
    s = settings.resolve(fast)
    if override:
        s.update({k: v for k, v in override.items() if v})
    if not (s["base_url"] and s["model"]):
        raise LLMError("未配置模型")
    url = s["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    s["url"] = url
    s["headers"] = {"Authorization": f"Bearer {s['api_key']}"} if s["api_key"] else {}
    return s


async def _post(s: dict, body: dict, timeout: float, retry_without: str | None = None) -> dict:
    """timeout 是整次调用的上限。httpx 的超时只管两次收到数据的间隔，DeepSeek 排队时会一直发空行保活，
    光靠它能挂好几分钟，所以外面再套一层总时长。"""
    try:
        return await asyncio.wait_for(_post_once(s, body, timeout, retry_without), timeout + 5)
    except asyncio.TimeoutError as e:
        _mark(False, "总时长超时")
        raise LLMError(f"ReadTimeout: 超过 {int(timeout)} 秒没回完") from e


async def _post_once(s: dict, body: dict, timeout: float, retry_without: str | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            r = await c.post(s["url"], json=body, headers=s["headers"])
            if r.status_code == 400 and retry_without and retry_without in body:
                body.pop(retry_without)
                r = await c.post(s["url"], json=body, headers=s["headers"])
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
    except LLMError as e:
        _mark(False, str(e))
        raise
    except Exception as e:  # 网络、超时、回包格式
        _mark(False, f"{type(e).__name__}: {e}")
        raise LLMError(f"{type(e).__name__}: {e}") from e
    _mark(True)
    return data


async def chat(messages: list[dict], *, fast: bool = False, json_mode: bool = True,
               timeout: float = 30.0, temperature: float = 0.2, override: dict | None = None) -> str:
    s = _target(fast, override)
    body = {"model": s["model"], "messages": messages, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = await _post(s, body, timeout, "response_format" if json_mode else None)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        _mark(False, f"回包格式不对：{str(data)[:200]}")
        raise LLMError("回包格式不对") from e


# DeepSeek 偶尔不走 tool_calls 字段，把工具调用写成正文里的 DSML 标记：
# <｜DSML｜tool_calls><｜DSML｜invoke name="x"><｜DSML｜parameter name="k" string="true">v</｜DSML｜parameter>…
_B = r"[｜|]+\s*DSML\s*[｜|]+"
DSML_MARK = re.compile(r"<" + _B + r"(?:tool_calls|invoke)")
_INVOKE = re.compile(r"<" + _B + r'invoke\s+name="([^"]+)"\s*>(.*?)(?=</' + _B + r"invoke>|<" + _B + r"invoke\s|\Z)", re.S)
_PARAM = re.compile(r"<" + _B + r'parameter\s+name="([^"]+)"(?:\s+string="(true|false)")?\s*>(.*?)(?:</' + _B + r"parameter>|\Z)", re.S)


def parse_dsml(text: str) -> tuple[list[dict], str]:
    """把正文里的 DSML 工具调用拆成标准 tool_calls；返回（调用列表，去掉标记后的正文）。"""
    m = DSML_MARK.search(text)
    if not m:
        return [], text
    head, tail = text[:m.start()].strip(), text[m.start():]
    calls = []
    for i, inv in enumerate(_INVOKE.finditer(tail)):
        args = {}
        for pm in _PARAM.finditer(inv.group(2)):
            v = pm.group(3)
            if pm.group(2) == "false":
                try:
                    v = json.loads(v)
                except json.JSONDecodeError:
                    pass
            args[pm.group(1)] = v
        calls.append({"id": f"dsml_{uuid.uuid4().hex[:8]}_{i}", "type": "function",
                      "function": {"name": inv.group(1).strip(), "arguments": json.dumps(args, ensure_ascii=False)}})
    return calls, head


def _transient(err: str) -> bool:
    """偶发的网络抖动或上游网关瞬断：值得自动重试。"""
    return any(k in (err or "") for k in (
        "Timeout", "ConnectError", "ConnectTimeout", "ReadError", "RemoteProtocolError",
        "HTTP 502", "HTTP 503", "HTTP 504",
    ))


async def chat_tools(messages: list[dict], tools: list[dict], *, timeout: float = 90.0,
                     temperature: float = 0.2, fast: bool = False) -> dict:
    """带工具定义调一次（OpenAI function calling）。返回 assistant 消息原样：content + tool_calls。"""
    s = _target(fast, None)
    body = {"model": s["model"], "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    data = None
    for attempt in range(3):
        try:
            data = await _post(s, body, timeout)
            break
        except LLMError as e:  # 偶尔一次卡住或瞬断：退避重试，大多马上就回
            if attempt >= 2 or not _transient(str(e)):
                raise
            await asyncio.sleep(1.5 * (attempt + 1))
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError("回包格式不对") from e
    out = {"role": "assistant", "content": msg.get("content") or ""}
    calls = msg.get("tool_calls") or []
    if calls:
        out["tool_calls"] = [{"id": c.get("id") or f"call_{i}", "type": "function",
                              "function": {"name": c["function"]["name"],
                                           "arguments": c["function"].get("arguments") or "{}"}}
                             for i, c in enumerate(calls)]
    if not calls and tools and DSML_MARK.search(out["content"]):
        text_calls, rest = parse_dsml(out["content"])
        if text_calls:
            out["content"] = rest
            out["tool_calls"] = text_calls
    if msg.get("reasoning_content"):  # DeepSeek 思考模式：下一轮要原样带回
        out["reasoning_content"] = msg["reasoning_content"]
    return out


def human_reason(err: str) -> str:
    """把底层报错翻成一句人话；原文放详情里。"""
    e = err or ""
    if "未配置" in e:
        return "还没填服务地址或模型"
    if "ConnectError" in e or "ConnectTimeout" in e or "NameResolution" in e:
        return "服务地址没有响应"
    if "Timeout" in e:
        return "等太久没有回应"
    if "HTTP 401" in e or "HTTP 403" in e:
        return "密钥不对或没有权限"
    if "HTTP 402" in e or "Insufficient Balance" in e or "余额" in e:
        return "模型账户余额不足，充值后再试"
    if "HTTP 404" in e:
        return "地址或模型名不对"
    if "HTTP 429" in e:
        return "调用太频繁或额度用完了"
    if "HTTP 5" in e:
        return "模型服务自己出错了"
    return "模型服务返回了看不懂的结果"


def parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            return json.loads(m.group(0))
        raise


async def chat_json(messages: list[dict], **kw) -> dict:
    return parse_json(await chat(messages, **kw))
