"""不依赖模型的中文文本相似度：字的二元组重合。规则兜底用。"""
from __future__ import annotations

import re

STOP = set("的了和与及或在把将对给也再还是就都要请帮我你他们这那个一些")


def grams(text: str) -> set[str]:
    t = re.sub(r"[\s\W_]+", " ", text or "").lower()
    out = set()
    for seg in t.split():
        if re.fullmatch(r"[a-z0-9.]+", seg):
            out.add(seg)
            continue
        chars = [c for c in seg if c not in STOP]
        out.update(a + b for a, b in zip(chars, chars[1:]))
    return out


def overlap(a: str, b: str) -> int:
    return len(grams(a) & grams(b))
