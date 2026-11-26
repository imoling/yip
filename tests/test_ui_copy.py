"""防回潮：界面文案的机器可判规矩（全局 CLAUDE.md「界面文案」）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CJK = r"[一-鿿]"
APP = ROOT / "app" / "src"
UI = [p for p in APP.rglob("*") if p.suffix in (".ts", ".tsx")]
FILES = UI + [p for p in (ROOT / "server").rglob("*.py")]
# 只看带中文的字符串字面量（含模板字符串和 HTML 文本）
LIT = re.compile(r"""(?s)(`[^`]*`|'[^'\n]*'|"[^"\n]*"|>[^<>]*<)""")
BANNED_UI = ["Base URL", "API Key", "llama", "SHA256", "点击", "请稍候", "温馨提示"]


def strings():
    for p in FILES:
        text = p.read_text("utf-8")
        for m in LIT.finditer(text):
            s = m.group(0)
            if re.search(CJK, s):
                line = text[: m.start()].count("\n") + 1
                yield p.relative_to(ROOT), line, s


def test_no_ascii_ellipsis():
    bad = [(str(p), n, s[:40]) for p, n, s in strings() if re.search(CJK + r"\.\.\.|\.\.\." + CJK, s)]
    assert not bad, bad


def test_no_halfwidth_colon_after_chinese():
    bad = [(str(p), n, s[:40]) for p, n, s in strings() if re.search(CJK + r"[:;,](?![/\d])", s)]
    assert not bad, bad


def test_no_banned_words_in_ui():
    # 「llama」防的是 llama-server 这类实现细节，Ollama 是服务商品牌名，放过
    bad = [(p.name, w) for p in UI for w in BANNED_UI
           if re.search(r"(?<![Oo])" + re.escape(w), p.read_text("utf-8"))]
    assert not bad, bad


def test_ui_sources_found():
    assert len(UI) > 10, "没扫到 app/src 的界面源码"


def test_placeholder_and_labels_have_no_trailing_period():
    src = "".join(p.read_text("utf-8") for p in UI)
    bad = re.findall(r'placeholder=[{"][^"]*。"', src) + re.findall(r"<button[^>]*>\s*[^<{]*。\s*</button>", src)
    assert not bad, bad
