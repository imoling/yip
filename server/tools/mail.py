"""邮件：内置 IMAP 读信、SMTP 发信。配置在 settings.json 的 mail 段（授权码 0600 存放，界面只显示掩码）。

- mail_search / mail_read：只读，直接做
- mail_draft：草稿写成工作文件夹里的 .eml，记成产物（WRITE）
- mail_send：对外发信，不管确认策略、不管「本次都允许」，每封都先问（risk.ALWAYS_ASK）

搜索为了兼容中文邮箱：服务器只按日期筛（SINCE 是纯 ASCII，各家都支持），发件人、主题、关键词在本地过滤。
163 / 126 不发 IMAP ID 会报「Unsafe Login」，连上后统一先发一次 ID。
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import re
import smtplib
import ssl
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parsedate_to_datetime

from .. import artifacts, settings
from . import EXTERNAL, READ, WRITE, Ctx, Tool, ToolError, clip, obj
from .web import html_to_text

PRESETS = {
    "qq": {"name": "QQ 邮箱", "imap": ("imap.qq.com", 993), "smtp": ("smtp.qq.com", 465), "help": "邮箱设置 → 账户 → 开启 IMAP/SMTP，生成授权码"},
    "163": {"name": "网易 163", "imap": ("imap.163.com", 993), "smtp": ("smtp.163.com", 465), "help": "设置 → POP3/SMTP/IMAP → 开启并获取授权码"},
    "exmail": {"name": "腾讯企业邮", "imap": ("imap.exmail.qq.com", 993), "smtp": ("smtp.exmail.qq.com", 465), "help": "设置 → 客户端设置 → 开启 IMAP，用客户端专用密码"},
    "aliyun": {"name": "阿里企业邮", "imap": ("imap.qiye.aliyun.com", 993), "smtp": ("smtp.qiye.aliyun.com", 465), "help": "设置 → 客户端设置 → 开启 IMAP"},
    "gmail": {"name": "Gmail", "imap": ("imap.gmail.com", 993), "smtp": ("smtp.gmail.com", 465), "help": "Google 账号 → 安全 → 应用专用密码"},
    "outlook": {"name": "Outlook", "imap": ("outlook.office365.com", 993), "smtp": ("smtp.office365.com", 587), "help": "账户要开 IMAP；企业账号可能要管理员放行"},
    "custom": {"name": "其他", "imap": ("", 993), "smtp": ("", 465), "help": "填邮箱服务商给的 IMAP / SMTP 地址"},
}


def config() -> dict:
    return dict(settings.load().get("mail") or {})


def configured() -> bool:
    c = config()
    return bool(c.get("address") and c.get("password") and c.get("imap_host"))


def _hdr(v) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:  # noqa: BLE001
        return v or ""


def _imap(c: dict) -> imaplib.IMAP4_SSL:
    try:
        m = imaplib.IMAP4_SSL(c["imap_host"], int(c.get("imap_port") or 993), timeout=20)
        m.login(c["address"], c["password"])
    except imaplib.IMAP4.error as e:
        raise ToolError(f"登录失败：{e}；检查邮箱和授权码，确认已开启 IMAP") from e
    except OSError as e:
        raise ToolError(f"连不上 {c['imap_host']}：{type(e).__name__}") from e
    try:  # 163 / 126 不发 ID 会拒绝选信箱
        imaplib.Commands.setdefault("ID", ("AUTH",))
        m._simple_command("ID", '("name" "Yip" "version" "1.0" "vendor" "iML Studio")')
    except Exception:  # noqa: BLE001
        pass
    return m


def _search(c: dict, query: str, sender: str, since_days: int, unread_only: bool, limit: int) -> list[dict]:
    m = _imap(c)
    try:
        m.select("INBOX", readonly=True)
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - max(1, since_days) * 86400))
        crit = f"(SINCE {since})" + (" UNSEEN" if unread_only else "")
        typ, data = m.search(None, crit)
        ids = (data[0] or b"").split()[-300:]
        out = []
        for uid in reversed(ids):
            typ, msg = m.fetch(uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
            if not msg or not isinstance(msg[0], tuple):
                continue
            flags = msg[0][0].decode(errors="replace")
            h = email.message_from_bytes(msg[0][1])
            row = {"id": uid.decode(), "from": _hdr(h.get("From")), "subject": _hdr(h.get("Subject")),
                   "date": h.get("Date", ""), "unread": "\\Seen" not in flags}
            hay = f"{row['from']} {row['subject']}".lower()
            if sender and sender.lower() not in row["from"].lower():
                continue
            if query and not all(w.lower() in hay for w in query.split()):
                continue
            out.append(row)
            if len(out) >= limit:
                break
        return out
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


def _body(msg: email.message.Message) -> tuple[str, list[str]]:
    text, html, files = "", "", []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        fn = part.get_filename()
        if fn:
            files.append(_hdr(fn))
            continue
        payload = part.get_payload(decode=True) or b""
        cs = part.get_content_charset() or "utf-8"
        try:
            s = payload.decode(cs, errors="replace")
        except LookupError:
            s = payload.decode("utf-8", errors="replace")
        if part.get_content_type() == "text/plain" and not text:
            text = s
        elif part.get_content_type() == "text/html" and not html:
            html = s
    return (text or html_to_text(html)).strip(), files


def _read(c: dict, uid: str) -> str:
    m = _imap(c)
    try:
        m.select("INBOX", readonly=True)
        typ, msg = m.fetch(uid.encode(), "(BODY.PEEK[])")
        if not msg or not isinstance(msg[0], tuple):
            raise ToolError(f"找不到这封信：{uid}")
        e = email.message_from_bytes(msg[0][1])
        body, files = _body(e)
        head = (f"发件人：{_hdr(e.get('From'))}\n收件人：{_hdr(e.get('To'))}\n"
                + (f"抄送：{_hdr(e.get('Cc'))}\n" if e.get("Cc") else "")
                + f"时间：{e.get('Date', '')}\n主题：{_hdr(e.get('Subject'))}\nMessage-ID：{e.get('Message-ID', '')}\n"
                + (f"附件：{'、'.join(files)}\n" if files else ""))
        return clip(f"{head}\n{body}", 12000)
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


def _compose(c: dict, to: str, subject: str, body: str, cc: str = "", reply_to_id: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((c.get("name") or "", c["address"]))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    if reply_to_id:
        msg["In-Reply-To"] = reply_to_id
        msg["References"] = reply_to_id
    msg.set_content(body)
    return msg


def _send(c: dict, msg: EmailMessage) -> None:
    host, port = c.get("smtp_host"), int(c.get("smtp_port") or 465)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context()) as s:
                s.login(c["address"], c["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(c["address"], c["password"])
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise ToolError("发信登录失败：检查授权码，确认已开启 SMTP") from e
    except (smtplib.SMTPException, OSError) as e:
        raise ToolError(f"没发出去：{type(e).__name__}: {str(e)[:120]}") from e


def _need() -> dict:
    if not configured():
        raise ToolError("还没配邮箱：请主人在「设置 · 邮箱」里填邮箱和授权码")
    return config()


async def mail_search(ctx: Ctx, query: str = "", sender: str = "", since_days: int = 7, unread_only: bool = False,
                      limit: int = 20) -> str:
    c = _need()
    rows = await asyncio.to_thread(_search, c, query or "", sender or "", int(since_days or 7), bool(unread_only),
                                   max(1, min(int(limit or 20), 50)))
    if not rows:
        return "这段时间里没找到符合条件的信"
    return "\n".join(f"[{r['id']}] {'●' if r['unread'] else ' '} {r['date'][:22]} | {r['from']} | {r['subject']}" for r in rows)


async def mail_read(ctx: Ctx, id: str) -> str:  # noqa: A002
    from .. import sentinel
    raw = await asyncio.to_thread(_read, _need(), str(id))
    out, _ = sentinel.wrap(raw, source=f"邮件 #{id}", task_id=ctx.task_id)
    return out


async def mail_draft(ctx: Ctx, to: str, subject: str, body: str, cc: str = "", reply_to_id: str = "") -> str:
    c = _need()
    msg = _compose(c, to, subject, body, cc, reply_to_id)
    folder = ctx.workspace / "邮件草稿"
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]', "_", subject or "无主题")[:40] or "无主题"
    path = folder / f"{time.strftime('%m%d-%H%M')} {name}.eml"
    path.write_bytes(bytes(msg))
    artifacts.record(ctx, path)
    return f"草稿已存：{path.relative_to(ctx.workspace)}（双击用邮件应用打开就能改了再发）"


async def mail_send(ctx: Ctx, to: str, subject: str, body: str, cc: str = "", reply_to_id: str = "") -> str:
    c = _need()
    if not getaddresses([to]) or "@" not in to:
        raise ToolError("收件人地址不对")
    await asyncio.to_thread(_send, c, _compose(c, to, subject, body, cc, reply_to_id))
    return f"已发给 {to}：{subject}"


def _brief_send(a: dict) -> str:
    body = re.sub(r"\s+", " ", a.get("body") or "")[:60]
    return f"发信给 {a.get('to', '')}｜{a.get('subject', '')}｜{body}"


TOOLS = [
    Tool("mail_search", "搜收件箱：按关键词（主题或发件人里有的字）、发件人、最近几天、只看未读筛，返回 [编号] 时间 | 发件人 | 主题。",
         obj({"query": {"type": "string", "description": "关键词，空格分开表示都要有"},
              "sender": {"type": "string", "description": "发件人名字或地址的一部分"},
              "since_days": {"type": "integer", "description": "最近几天，默认 7"},
              "unread_only": {"type": "boolean", "description": "只看未读"},
              "limit": {"type": "integer", "description": "最多几封，默认 20"}}, []),
         mail_search, READ, lambda a: f"搜邮件「{a.get('query') or a.get('sender') or '最近的'}」", "搜邮件"),
    Tool("mail_read", "读一封信的全文（用 mail_search 给的编号）。",
         obj({"id": {"type": "string", "description": "mail_search 结果里方括号中的编号"}}),
         mail_read, READ, lambda a: f"读邮件 {a.get('id', '')}", "读邮件"),
    Tool("mail_draft", "起草一封信，存成工作文件夹里的 .eml 草稿，不发出去。回复别人时带上原信的 Message-ID。",
         obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string", "description": "正文，纯文本"},
              "cc": {"type": "string"}, "reply_to_id": {"type": "string", "description": "回复时填原信的 Message-ID"}},
             ["to", "subject", "body"]),
         mail_draft, WRITE, lambda a: f"起草邮件给 {a.get('to', '')}｜{a.get('subject', '')}", "起草邮件"),
    Tool("mail_send", "直接发出一封信。会先请主人确认；主人只是让你起草时用 mail_draft，别用这个。",
         obj({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
              "cc": {"type": "string"}, "reply_to_id": {"type": "string"}}, ["to", "subject", "body"]),
         mail_send, EXTERNAL, _brief_send, "发邮件"),
]


async def test(c: dict) -> dict:
    """「测试连接」：登录 IMAP 数一下最近 7 天的信，再登录 SMTP（不发信）。"""
    def go():
        m = _imap(c)
        try:
            m.select("INBOX", readonly=True)
            since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - 7 * 86400))
            n = len((m.search(None, f"(SINCE {since})")[1][0] or b"").split())
        finally:
            m.logout()
        host, port = c.get("smtp_host"), int(c.get("smtp_port") or 465)
        if host:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                    s.login(c["address"], c["password"])
            else:
                with smtplib.SMTP(host, port, timeout=20) as s:
                    s.starttls()
                    s.login(c["address"], c["password"])
        return n
    try:
        n = await asyncio.to_thread(go)
        return {"ok": True, "text": f"收发都通了，最近 7 天收件箱有 {n} 封"}
    except ToolError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}"}


def when(d: str) -> float:
    try:
        return parsedate_to_datetime(d).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0
