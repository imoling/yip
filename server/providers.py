"""模型服务预置：只收 OpenAI 兼容接口。写法参照 OpenWorker（MIT）的 coworker/providers/registry.py。

一条预置 = 一个服务：名称、默认地址、推荐的主模型 / 快模型、去哪拿密钥。
界面按这张表画卡片墙，加服务只改这里。模型名只是推荐值，界面上可以改。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    base_url: str
    model: str = ""
    fast_model: str = ""
    key_url: str = ""
    needs_key: bool = True
    note: str = ""          # 界面副文案：只写不填会怎样 / 例外


PRESETS: list[Preset] = [
    Preset("deepseek", "DeepSeek", "https://api.deepseek.com", "deepseek-v4-pro", "deepseek-flash",
           "https://platform.deepseek.com/api_keys"),
    Preset("xfyun", "讯飞星火", "https://spark-api-open.xf-yun.com/v1", "4.0Ultra", "lite",
           "https://console.xfyun.cn/services/bm4"),
    Preset("qwen", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "qwen-turbo",
           "https://bailian.console.aliyun.com/?apiKey=1"),
    Preset("zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-5.2", "glm-4-flash",
           "https://open.bigmodel.cn/usercenter/apikeys"),
    Preset("kimi", "Kimi", "https://api.moonshot.cn/v1", "kimi-k2.6", "kimi-k2.6",
           "https://platform.moonshot.cn/console/api-keys"),
    Preset("doubao", "豆包", "https://ark.cn-beijing.volces.com/api/v3", "", "",
           "https://console.volcengine.com/ark", note="模型名填方舟里的推理接入点 ID"),
    Preset("minimax", "MiniMax", "https://api.minimaxi.com/v1", "MiniMax-M2.5", "MiniMax-M2.5",
           "https://platform.minimaxi.com/user-center/basic-information/interface-key"),
    Preset("openai", "OpenAI", "https://api.openai.com/v1", "gpt-5.6-sol", "gpt-5.6-sol",
           "https://platform.openai.com/api-keys"),
    Preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "z-ai/glm-5.2", "z-ai/glm-5.2",
           "https://openrouter.ai/keys"),
    Preset("ollama", "Ollama（本机）", "http://localhost:11434/v1", "qwen3:8b", "qwen3:8b",
           needs_key=False, note="模型跑在这台电脑上，要先装好 Ollama 并拉取模型"),
    Preset("custom", "自定义", "", "", "", note="任何 OpenAI 兼容接口"),
]

BY_ID = {p.id: p for p in PRESETS}


def guess(base_url: str) -> str:
    """旧配置只有一个地址：按地址认出是哪家，认不出算自定义。"""
    u = (base_url or "").rstrip("/").lower()
    for p in PRESETS:
        if p.base_url and u.startswith(p.base_url.rstrip("/").lower().rsplit("/v", 1)[0]):
            return p.id
    return "custom"


def public() -> list[dict]:
    return [asdict(p) for p in PRESETS]
