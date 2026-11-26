"""常用连接器预置：点一下、填一两个空就能接上。

每条：名称、一句话用途、要填的空（fields）、配置模板（{字段} 会被替换）、是否默认先问。
本机命令类要先装好 Node（npx）或 uv（uvx）；网络地址类只要密钥。
"""
from __future__ import annotations

import copy
import os
import re
import shutil

PRESETS: list[dict] = [
    {"id": "filesystem", "name": "文件系统", "desc": "读写指定文件夹（工作文件夹以外的也行）", "needs": "npx",
     "fields": [{"key": "path", "label": "允许访问的文件夹", "default": "~/Documents"}],
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{path}"]}},
    {"id": "fetch", "name": "网页抓取", "desc": "把网页转成 Markdown，比内置读网页更全", "needs": "uvx", "fields": [],
     "config": {"command": "uvx", "args": ["mcp-server-fetch"]}, "requires_approval": False},
    {"id": "playwright", "name": "浏览器", "desc": "开一个真浏览器点页面、填表、截图", "needs": "npx", "fields": [],
     "config": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]}},
    {"id": "time", "name": "时间与时区", "desc": "查当前时间、换算时区", "needs": "uvx", "fields": [],
     "config": {"command": "uvx", "args": ["mcp-server-time", "--local-timezone=Asia/Shanghai"]}, "requires_approval": False},
    {"id": "sqlite", "name": "SQLite 数据库", "desc": "查询和分析本地 .db 文件", "needs": "uvx",
     "fields": [{"key": "db", "label": "数据库文件", "default": "~/Documents/Yip/data.db"}],
     "config": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "{db}"]}},
    {"id": "git", "name": "Git 仓库", "desc": "看提交记录、差异、分支", "needs": "uvx",
     "fields": [{"key": "repo", "label": "仓库路径", "default": "~/imoling.git"}],
     "config": {"command": "uvx", "args": ["mcp-server-git", "--repository", "{repo}"]}},
    {"id": "amap", "name": "高德地图", "desc": "地点搜索、路线规划、天气", "needs": "",
     "fields": [{"key": "key", "label": "高德 Web 服务 Key", "secret": True, "url": "https://console.amap.com/dev/key/app"}],
     "config": {"url": "https://mcp.amap.com/mcp?key={key}"}, "requires_approval": False},
    {"id": "github", "name": "GitHub", "desc": "查仓库、Issue、PR，读代码", "needs": "",
     "fields": [{"key": "token", "label": "个人访问令牌", "secret": True, "url": "https://github.com/settings/personal-access-tokens"}],
     "config": {"url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer {token}"}}},
    {"id": "lark", "name": "飞书", "desc": "读写飞书文档、多维表格、消息", "needs": "npx",
     "fields": [{"key": "app_id", "label": "App ID", "url": "https://open.feishu.cn/app"},
                {"key": "app_secret", "label": "App Secret", "secret": True}],
     "config": {"command": "npx", "args": ["-y", "@larksuiteoapi/lark-mcp", "mcp", "-a", "{app_id}", "-s", "{app_secret}"]}},
    {"id": "notion", "name": "Notion", "desc": "搜索和编辑 Notion 页面与数据库", "needs": "npx",
     "fields": [{"key": "token", "label": "Integration Token", "secret": True, "url": "https://www.notion.so/profile/integrations"}],
     "config": {"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"], "env": {"NOTION_TOKEN": "{token}"}}},
    {"id": "context7", "name": "Context7 文档", "desc": "查各种开发库的最新文档", "needs": "", "fields": [],
     "config": {"url": "https://mcp.context7.com/mcp"}, "requires_approval": False},
    {"id": "tavily", "name": "Tavily 搜索", "desc": "深度搜索：冷门、最新、要出处的查得准，平时的搜不到也换它", "needs": "",
     "fields": [{"key": "key", "label": "Tavily API Key", "secret": True, "url": "https://app.tavily.com/home"}],
     "config": {"url": "https://mcp.tavily.com/mcp/?tavilyApiKey={key}"}, "requires_approval": False},
    {"id": "brave", "name": "Brave 搜索", "desc": "换一个搜索源，结果比免费搜索稳", "needs": "npx",
     "fields": [{"key": "key", "label": "Brave Search API Key", "secret": True, "url": "https://brave.com/search/api/"}],
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"], "env": {"BRAVE_API_KEY": "{key}"}},
     "requires_approval": False},
    {"id": "apple-calendar", "name": "日历与提醒", "desc": "读本机 macOS 日历和提醒事项，数据不上传云端", "needs": "npx", "fields": [],
     "config": {"command": "npx", "args": ["-y", "apple-mcp@latest"]}, "requires_approval": False},
    {"id": "dingtalk", "name": "钉钉", "desc": "查钉钉消息、日程和文档", "needs": "npx",
     "fields": [{"key": "app_key", "label": "AppKey", "url": "https://open-dev.dingtalk.com/"},
                {"key": "app_secret", "label": "AppSecret", "secret": True}],
     "config": {"command": "npx", "args": ["-y", "dingtalk-mcp"], "env": {"DINGTALK_APP_KEY": "{app_key}", "DINGTALK_APP_SECRET": "{app_secret}"}}},
    {"id": "wecom", "name": "企业微信", "desc": "发群消息、查通讯录和日程", "needs": "npx",
     "fields": [{"key": "corp_id", "label": "企业 ID", "url": "https://work.weixin.qq.com/"},
                {"key": "corp_secret", "label": "应用 Secret", "secret": True}],
     "config": {"command": "npx", "args": ["-y", "wecom-mcp"], "env": {"WECOM_CORP_ID": "{corp_id}", "WECOM_CORP_SECRET": "{corp_secret}"}}},
]

BY_ID = {p["id"]: p for p in PRESETS}


def _fill(v, values: dict):
    if isinstance(v, str):
        return re.sub(r"\{(\w+)\}", lambda m: os.path.expanduser(str(values.get(m.group(1), ""))) if m.group(1) in ("path", "db", "repo")
                      else str(values.get(m.group(1), "")), v)
    if isinstance(v, list):
        return [_fill(x, values) for x in v]
    if isinstance(v, dict):
        return {k: _fill(x, values) for k, x in v.items()}
    return v


def build(pid: str, values: dict) -> dict:
    p = BY_ID[pid]
    vals = {f["key"]: (values.get(f["key"]) or f.get("default") or "").strip() for f in p["fields"]}
    missing = [f["label"] for f in p["fields"] if not vals[f["key"]]]
    if missing:
        raise ValueError(f"要填：{'、'.join(missing)}")
    cfg = _fill(copy.deepcopy(p["config"]), vals)
    cfg["requires_approval"] = p.get("requires_approval", True)
    cfg["preset"] = pid
    return cfg


def public() -> list[dict]:
    from .shell import login_path
    path = login_path()
    out = []
    for p in PRESETS:
        need = p.get("needs")
        ok = not need or bool(shutil.which(need, path=path))
        out.append({k: v for k, v in p.items() if k != "config"} | {"ready": ok})
    return out
