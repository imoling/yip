from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Intent = Literal["augment", "append", "overturn"]


class Decision(BaseModel):
    intent: Intent
    target: str | None = None
    reason: str = ""
    cost: Literal["low", "mid", "high"] = "low"
    confidence: float = 0.0
    need_confirm: bool = False
    detail: str | None = None        # augment=新约束；append=新的一件事；overturn=新目标
    source: Literal["model", "rule", "fallback"] = "model"
    unsure: bool = False             # 没把握，先排着


class NewTask(BaseModel):
    goal: str


class Interject(BaseModel):
    text: str
    task_id: str | None = None


class Override(BaseModel):
    intent: Intent


class Assign(BaseModel):
    worker_id: str
    confirm: bool = False
