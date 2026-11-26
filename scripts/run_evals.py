# Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
"""考题：用真模型把几件典型的事各跑一遍，自动判分，出一份报告。改 harness 前后各跑一次，看通过率。

    .venv/bin/python scripts/run_evals.py                 # 全部
    .venv/bin/python scripts/run_evals.py snake team      # 只跑这几题

模型配置从桌面版的数据目录拷一份（~/Library/Application Support/cn.imoling.yip/settings.json），
数据和工作文件夹都在 ~/Library/Caches/yip-evals/<时间>/，不碰你的对话和文件；确认策略改成全部放行。
报告写到 evals/results/<时间>.md。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import glob
import json
import os
import shutil
import socket
import hashlib
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
APP = Path.home() / "Library" / "Application Support" / "cn.imoling.yip"
# 精确到秒并带上引擎：两轮同一分钟开跑也不会写进同一个文件夹
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{os.environ['SA_ENGINE']}" if os.environ.get("SA_ENGINE") else "")
RUN = Path.home() / "Library" / "Caches" / "yip-evals" / STAMP
JOB_TIMEOUT = 15 * 60


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def boot() -> tuple[subprocess.Popen, str]:
    (RUN / "data").mkdir(parents=True, exist_ok=True)
    (RUN / "ws").mkdir(parents=True, exist_ok=True)
    src = APP / "settings.json"
    if not src.exists():
        sys.exit("桌面版还没配模型：先在 Yip 的设置里配好再跑")
    d = json.loads(src.read_text("utf-8"))
    d.setdefault("yip", {})["approval"] = "allow_all"
    (RUN / "data" / "settings.json").write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    for sub in ("workers", "skills"):
        if (APP / sub).exists():
            shutil.copytree(APP / sub, RUN / "data" / sub, dirs_exist_ok=True)
    port = free_port()
    env = {**os.environ, "SA_DATA_DIR": str(RUN / "data"), "SA_WORKSPACE": str(RUN / "ws")}
    env.pop("SA_API_TOKEN", None)
    log = open(RUN / "server.log", "w")
    p = subprocess.Popen([str(ROOT / ".venv/bin/uvicorn"), "server.main:app", "--port", str(port)],
                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(url + "/api/health", timeout=1, trust_env=False).status_code == 200:
                return p, url
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    p.kill()
    sys.exit("后端没起来，看 " + str(RUN / "server.log"))


async def judge(rubric: str, files: list[Path], reply: str, mach: list[str]) -> tuple[bool, str]:
    sys.path.insert(0, str(ROOT))
    os.environ["SA_DATA_DIR"] = str(RUN / "data")
    from server import llm, verify
    r = await llm.chat_json([{"role": "user", "content": verify.JUDGE.format(
        today=verify.today(), main_goal="（考题）", goal=rubric, accept=f"- {rubric}", cons="无", reply=reply[:3000], machine="\n".join(mach) or "没发现问题（查过文件在不在、网页能否完整加载且无报错、脚本语法）",
        files=verify._excerpts(files, 20000))}], timeout=180)
    return bool(r.get("pass")), "；".join(r.get("problems") or [])[:300] + f"（{r.get('score')} 分）"


async def run_case(c: httpx.AsyncClient, case: dict) -> dict:
    t0 = time.time()
    out = {"id": case["id"], "say": case["say"], "ok": True, "notes": []}

    def fail(msg):
        out["ok"] = False
        out["notes"].append(msg)

    if case.get("setup") == "two_pdfs":  # 合并 PDF 的素材：a 两页、b 一页，每页写上记号
        d = RUN / "ws" / "素材"
        d.mkdir(parents=True, exist_ok=True)
        script = ("import sys\nfrom reportlab.pdfgen import canvas\n"
                  "for name, pages in (('a', 2), ('b', 1)):\n"
                  "    c = canvas.Canvas(f'{sys.argv[1]}/{name}.pdf')\n"
                  "    for i in range(pages):\n"
                  "        c.drawString(100, 700, f'{name.upper()}-page-{i + 1}'); c.showPage()\n"
                  "    c.save()\n")
        subprocess.run(["python3", "-c", script, str(d)], check=True)
        out["_md5"] = {n: hashlib.md5((d / n).read_bytes()).hexdigest() for n in ("a.pdf", "b.pdf")}
    r = (await c.post("/api/say", json={"text": case["say"]})).json()
    if r["kind"] != case["kind"]:
        fail(f"分流成 {r['kind']}，应该是 {case['kind']}")
    if r["kind"] == "schedule" and case["kind"] == "schedule":
        autos = (await c.get("/api/automations")).json()
        a = next((x for x in autos if x["id"] == r.get("automation")), None)
        if not a:
            fail("没建出自动化")
        else:
            out["notes"].append(f"{a['when']}：{a['text']}")
            if case.get("rule") and a["rule"] != case["rule"]:
                fail(f"规则是 {a['rule']}，应该是 {case['rule']}")
            if case.get("rule_type") and a["rule"]["type"] != case["rule_type"]:
                fail(f"规则类型是 {a['rule']['type']}")
            if case.get("text_has") and case["text_has"] not in a["text"]:
                fail(f"要做的事里没有「{case['text_has']}」")
    if r["kind"] == "chat" and case["kind"] == "chat":
        for _ in range(120):
            msgs = (await c.get("/api/state")).json()["messages"]
            last = msgs[-1]
            if last["role"] == "assistant":
                break
            await asyncio.sleep(0.5)
        out["notes"].append(last["content"][:80].replace("\n", " "))
        if case.get("reply_has") and case["reply_has"] not in last["content"]:
            fail(f"回答里没有「{case['reply_has']}」")
    if r["kind"] == "new" and case["kind"] == "new":
        jid = r["job"]
        said_go = False
        while time.time() - t0 < JOB_TIMEOUT:
            t = (await c.get(f"/api/tasks/{jid}")).json()
            task = t.get("task") or t
            if task["status"] in ("done", "failed", "cancelled"):
                break
            if task.get("note") == "等你点头" and not said_go:  # 先对大纲的活：像主人一样看完说开工
                await c.post("/api/say", json={"text": "开工"})
                said_go = True
                out["notes"].append("对过大纲，说了开工")
            await asyncio.sleep(5)
        else:
            fail("超时没做完")
        detail = (await c.get(f"/api/tasks/{jid}")).json()
        task = detail.get("task") or detail
        folder = RUN / "ws" / task["title"].replace("/", "-")
        out["notes"].append(f"「{task['title']}」{task['status']}，{task.get('note') or ''}")
        kids = detail.get("children") or []
        reworks = sum(((k.get("verdict") or {}).get("reworks") or 0) for k in kids)
        out["reworks"] = reworks
        who = [k.get("worker_id") for k in kids]
        if case.get("worker") and case["worker"] not in who:
            fail(f"派给了 {'、'.join(filter(None, who))}，应该是 {case['worker']}")
        found: list[Path] = []
        for pat in case.get("files", []):
            hits = [Path(x) for x in glob.glob(str(folder / "**" / pat), recursive=True)]
            hits = [h for h in hits if h.name != f"{task['title']}.md" or pat != "*.md"] or hits
            if not hits:  # 有的活把结果放回素材旁边（比如合并 PDF）：工作文件夹里这次新写的也算
                hits = [Path(x) for x in glob.glob(str(RUN / "ws" / "**" / pat), recursive=True)
                        if os.path.getmtime(x) > t0 and not (case.get("setup") and Path(x).name in ("a.pdf", "b.pdf"))]
            if not hits:
                fail(f"没有 {pat}")
            found += hits
        # 评审要看到说明文档：考题只点名了 *.py 时，README 和交付的 md 也一起给评审（上次评审没看到 README 就判「缺说明」）
        found += [h for h in folder.glob("*.md") if h not in found and not h.name.startswith(("A-", "B-", "C-", "D-"))]
        probs = []
        for n, h in (out.pop("_md5", None) or {}).items():  # 素材不许被改
            f = RUN / "ws" / "素材" / n
            if not f.exists() or hashlib.md5(f.read_bytes()).hexdigest() != h:
                fail(f"原文件 {n} 被改动或删掉了")
        if case.get("machine") and found:
            from server import verify
            probs = await verify.machine(found, "")
            if probs:
                fail("机器检查：" + "；".join(probs)[:200])
        if case.get("rubric") and found:
            ok, why = await judge(case["rubric"], found, task.get("result") or "", probs)
            out["notes"].append("评审：" + why)
            if not ok:
                fail("评审没过")
    out["secs"] = round(time.time() - t0)
    return out


async def main(only: list[str]) -> None:
    cases = [json.loads(ln) for ln in (ROOT / "evals" / "cases.jsonl").read_text("utf-8").splitlines() if ln.strip()]
    if only:
        cases = [c for c in cases if c["id"] in only]
    proc, url = boot()
    sys.path.insert(0, str(ROOT))
    os.environ["SA_DATA_DIR"] = str(RUN / "data")
    results = []
    try:
        async with httpx.AsyncClient(base_url=url, timeout=60, trust_env=False) as c:
            quick = [x for x in cases if x["kind"] != "new"]  # noqa
            slow = [x for x in cases if x["kind"] == "new"]
            for case in quick + slow:  # 分流类的先来、一条条来，免得互相当成插话
                try:
                    results.append(await run_case(c, case))
                except Exception as e:  # noqa: BLE001 — 一题出错（比如余额不足）不拖垮整份报告
                    results.append({"id": case["id"], "say": case["say"], "ok": False, "secs": 0,
                                    "notes": [f"跑题出错：{str(e)[:160]}"]})
                print(("✓ " if results[-1]["ok"] else "✗ ") + case["id"], flush=True)
    finally:
        proc.terminate()
    ok = sum(r["ok"] for r in results)
    eng = os.environ.get("SA_ENGINE") or "按设置"
    lines = [f"# 考题结果 {STAMP}（队员引擎：{eng}）", "", f"通过 {ok} / {len(results)}；数据和产物在 `{RUN}`", "",
             "| 题 | 结果 | 用时 | 返工 | 说明 |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {'通过' if r['ok'] else '没过'} | {r['secs']} 秒 | {r.get('reworks', '')} | "
                     + "<br>".join(n.replace("|", "\\|") for n in r["notes"]) + " |")
    dest = ROOT / "evals" / "results" / f"{STAMP}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"\n通过 {ok} / {len(results)}，报告：{dest}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
