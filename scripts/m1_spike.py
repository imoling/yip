"""M1 实验：运行中 update_state 能否被下一轮读到；cancel 后同 thread 新 run 是否从头开始。"""
import asyncio, operator, sys
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

class S(TypedDict):
    goal: str
    constraints: list[str]
    step: int
    log: list[str]

async def act(s: S):
    await asyncio.sleep(0.3)
    return {"step": s["step"] + 1, "log": s["log"] + [f"{s['goal']} step{s['step']+1} c={s['constraints']}"]}

def route(s: S):
    return END if s["step"] >= 5 else "act"

async def main(path):
    async with AsyncSqliteSaver.from_conn_string(path) as cp:
        g = StateGraph(S); g.add_node("act", act); g.add_edge(START, "act"); g.add_conditional_edges("act", route)
        app = g.compile(checkpointer=cp)
        cfg = {"configurable": {"thread_id": "task:a"}}
        async def run(goal):
            async for _ in app.astream({"goal": goal, "constraints": [], "step": 0, "log": []}, cfg):
                pass
        t = asyncio.create_task(run("定价"))
        await asyncio.sleep(0.75)
        st = await app.aget_state(cfg)
        await app.aupdate_state(cfg, {"constraints": st.values["constraints"] + ["只看企业版"]})
        await t
        print("== update_state during run:")
        for l in (await app.aget_state(cfg)).values["log"]: print("  ", l)
        t = asyncio.create_task(run("定价2"))
        await asyncio.sleep(0.5); t.cancel()
        try: await t
        except asyncio.CancelledError: pass
        print("== after cancel step:", (await app.aget_state(cfg)).values["step"])
        await run("服务政策")
        v = (await app.aget_state(cfg)).values
        print("== new run on same thread:", v["goal"], v["step"], v["log"][0])

asyncio.run(main(sys.argv[1]))
