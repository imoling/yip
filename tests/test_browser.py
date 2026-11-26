"""队员的浏览器：打开本地网页、按编号点和填、自动模式下只有提交 / 付款 / 填密码才问。用本机 Chrome 无头跑，没有就跳过。"""
import pytest

from server.tools import Ctx, browser, risk

pytestmark = pytest.mark.skipif(not browser.available(), reason="没有 Playwright 或 Chrome")

PAGE = """<html><head><title>测试页</title></head><body>
<h1>待办</h1><input id=q placeholder="搜索待办"><button onclick="document.getElementById('out').textContent='搜到了：'+document.getElementById('q').value">搜索</button>
<a href="#next">下一页</a><p id=out></p></body></html>"""


@pytest.mark.parametrize("tool,args,want", [
    ("browser_click", {"ref": 3, "what": "下一页"}, "allow"),
    ("browser_click", {"ref": 3, "what": "提交订单"}, "ask"),
    ("browser_click", {"ref": 3, "what": "立即支付"}, "ask"),
    ("browser_type", {"ref": 1, "what": "搜索框", "text": "阿森纳", "submit": True}, "allow"),
    ("browser_type", {"ref": 1, "what": "密码", "text": "x"}, "ask"),
    ("browser_type", {"ref": 1, "what": "留言", "text": "你好", "submit": True}, "ask"),
])
def test_browser_risk(tool, args, want):
    assert risk.decide(tool, "write", "auto", [], args, "/ws") == want
    assert risk.decide(tool, "write", "ask_writes", [], args, "/ws") == "ask"
    assert risk.decide("browser_login", "read", "ask_all") == "allow", "请主人登录这步自己会问"


async def test_open_click_type(tmp_path, fresh_db):
    f = tmp_path / "t.html"
    f.write_text(PAGE, "utf-8")
    ctx = Ctx("t1", "m1", "w", tmp_path)
    try:
        snap = await browser.browser_open(ctx, f.as_uri())
        assert "测试页" in snap and "[1] 输入框「搜索待办」" in snap and "按钮「搜索」" in snap
        await browser.browser_type(ctx, 1, "阿森纳", "搜索框")
        out = await browser.browser_click(ctx, 2, "搜索")
        assert "搜到了：阿森纳" in out
        assert browser.B.state()["open"] and (await browser.B.shot())[:2] == b"\xff\xd8", "右栏能拿到画面"
    finally:
        await browser.B.close()


async def test_screenshot_saved_as_artifact(tmp_path, fresh_db):
    from server import db
    f = tmp_path / "t.html"
    f.write_text(PAGE, "utf-8")
    db.insert("tasks", id="t2", parent_id=None, label="", title="截图测试", goal="g", status="running",
              created_at=db.now(), updated_at=db.now())
    ctx = Ctx("t2", "t2", "w", tmp_path)
    try:
        await browser.browser_open(ctx, f.as_uri())
        out = await browser.browser_screenshot(ctx, "截图测试/页面.png")
        assert "截好了" in out and (tmp_path / "截图测试/页面.png").exists()
        assert db.q("SELECT * FROM artifacts WHERE name='页面.png'")
    finally:
        await browser.B.close()


async def test_judge_sees_image_facts(tmp_path):
    from PIL import Image
    from server import verify
    good, blank = tmp_path / "a.png", tmp_path / "b.png"
    im = Image.new("RGB", (1280, 800), "white")
    for x in range(0, 1280, 40):
        for y in range(200, 260):
            im.putpixel((x, y), (0, 0, 0))
    im.save(good)
    Image.new("RGB", (100, 100), "white").save(blank)
    assert await verify.machine([good], "") == []
    assert await verify.machine([blank], "") == ["b.png 是一片空白"]
    assert "1280×800，有内容，不是空白" in verify._excerpts([good])
