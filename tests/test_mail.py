"""邮件：本地过滤搜索、读信解码、草稿落 .eml、发信不管策略都先问。"""
import email.message

import pytest

from server import db, settings
from server.tools import Ctx, ToolError, mail, risk


class FakeIMAP:
    def __init__(self, msgs):
        self.msgs = msgs

    def select(self, *a, **k):
        return "OK", [b"3"]

    def search(self, charset, crit):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.msgs)))]

    def fetch(self, uid, what):
        m = self.msgs[int(uid) - 1]
        flags = b"1 (FLAGS (\\Seen)" if m.get("seen") else b"1 (FLAGS ()"
        return "OK", [(flags, m["raw"]), b")"]

    def logout(self):
        pass


def raw(frm, subj, body):
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, "me@x.com", subj, "Tue, 18 Nov 2026 09:00:00 +0800"
    m["Message-ID"] = "<abc@x>"
    m.set_content(body)
    return bytes(m)


@pytest.fixture
def box(fresh_db, monkeypatch):
    settings.save({"mail": {"provider": "qq", "address": "me@x.com", "password": "secret", "imap_host": "imap.qq.com",
                            "imap_port": 993, "smtp_host": "smtp.qq.com", "smtp_port": 465}})
    msgs = [{"raw": raw("张三 <z@a.com>", "周报：第 47 周", "本周完成三件事"), "seen": True},
            {"raw": raw("李四 <l@b.com>", "合同评审会议", "周四下午三点")}]
    monkeypatch.setattr(mail, "_imap", lambda c: FakeIMAP(msgs))
    sent = []
    monkeypatch.setattr(mail, "_send", lambda c, m: sent.append(m))
    return sent


async def test_search_read_draft_send(box, tmp_path):
    ctx = Ctx("t", "m", "w", tmp_path)
    out = await mail.mail_search(ctx, query="合同")
    assert "合同评审会议" in out and "周报" not in out and "●" in out, "未读打圆点"
    assert "周报" in await mail.mail_search(ctx, sender="张三")
    full = await mail.mail_read(ctx, "1")
    assert "本周完成三件事" in full and "Message-ID" in full
    db.insert("tasks", id="t", parent_id=None, label="", title="t", goal="g", status="running", created_at=db.now(), updated_at=db.now())
    d = await mail.mail_draft(ctx, to="l@b.com", subject="回复：合同评审会议", body="收到，准时参加")
    assert "邮件草稿" in d and list((tmp_path / "邮件草稿").glob("*.eml"))
    assert await mail.mail_send(ctx, to="l@b.com", subject="hi", body="正文")
    assert box[0]["To"] == "l@b.com"
    with pytest.raises(ToolError):
        await mail.mail_send(ctx, to="不是地址", subject="x", body="y")


def test_send_always_asks():
    for policy in ("allow_all", "ask_writes", "ask_all"):
        assert risk.decide("mail_send", "external", policy, ["mail_send", "*"]) == "ask"
    assert risk.decide("mail_search", "read", "ask_writes") == "allow"


def test_password_masked(box):
    pub = settings.public()["mail"]
    assert pub["password"] == settings.MASK and pub["address"] == "me@x.com"
    settings.save({"mail": {"password": settings.MASK}})
    assert settings.load()["mail"]["password"] == "secret", "掩码不覆盖真密码"


async def test_not_configured(fresh_db, tmp_path):
    with pytest.raises(ToolError):
        await mail.mail_search(Ctx("t", "m", "w", tmp_path))
