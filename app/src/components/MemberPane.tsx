// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 右栏里看一个队员在这件事里的进展：现在在干嘛、这块活和验收标准、一步一步做了什么、交了哪些文件。
// 对话里点队员的名片、看板里点队员那一行都开这里；跟着状态实时刷新。
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AppState, Artifact, Task, TaskEvent, TeamMsg, Worker } from "../types";
import { fileBadge, isLive, pct, statusText, fileTone } from "../util";
import { avatarOf } from "./Avatar";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

interface Props {
  taskId: string;
  state: AppState;
  workers: Worker[];
  onClose: () => void;
  onOpenFile: (a: Artifact) => void;
}

type Detail = { events: TaskEvent[]; team: TeamMsg[] };
type P = { text?: string; output?: string; label?: string; problems?: string[]; artifact_id?: number };

const SKIP = new Set(["created", "planned", "dispatched", "summarized", "memorized", "queued", "constraint_added"]);
const hm = (ms: number) => new Date(ms).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

export function MemberPane({ taskId, state, workers, onClose, onOpenFile }: Props) {
  const kid: Task | undefined = state.jobs.flatMap((j) => j.children).find((k) => k.id === taskId);
  const job = state.jobs.find((j) => j.children.some((k) => k.id === taskId));
  const [d, setD] = useState<Detail | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [goal, setGoal] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    let off = false;
    api<Detail>(`/api/tasks/${taskId}`).then((r) => { if (!off) setD(r); }).catch(() => undefined);
    return () => { off = true; };
  }, [taskId, kid?.updated_at, kid?.note, kid?.status]);

  const evs = (d?.events || []).filter((e) => e.task_id === taskId && !SKIP.has(e.type)).slice().reverse();
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [evs.length]);

  if (!kid) {
    return (
      <aside className="rail fpane mpane">
        <div className="rh"><button className="icon-btn" onClick={onClose} title="回到进度"><Icon name="back" /></button><span className="fpn">这个队员的活已经不在了</span></div>
      </aside>
    );
  }
  const card = workers.find((w) => w.id === kid.worker_id);
  const team = (d?.team || []).filter((t) => t.from_task === kid.id || t.to_task === kid.id);
  const files = state.artifacts.filter((a) => a.task_id === taskId);
  const live = isLive(kid);

  return (
    <aside className="rail fpane mpane">
      <div className="rh" data-tauri-drag-region>
        <button className="icon-btn" onClick={onClose} title="回到进度"><Icon name="back" /></button>
        <img className="mav" src={avatarOf(kid.worker_id, workers)} alt="" />
        <span className="fpn">{card?.name || kid.worker_id}<small>{kid.title}</small></span>
      </div>
      <div className="fbody mbody" ref={box} onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}>
        <div className={`mnow ${live ? "live" : kid.status}`}>
          <span className="dot" />
          <span className="t">{live ? kid.note || "在做" : statusText(kid)}</span>
          {live && <span className="p">{pct(kid.progress)}</span>}
        </div>
        {job && <div className="mjob">属于「{job.title}」</div>}
        <button className="mgoal" onClick={() => setGoal(!goal)}>
          这块活{goal ? "" : "：" + kid.goal.split("——").pop()?.slice(0, 40) + (kid.goal.length > 40 ? "…" : "")}
          <Icon name={goal ? "chev" : "right"} />
        </button>
        {goal && (
          <div className="mgoalx sel">
            {kid.goal}
            {kid.accept?.length ? <ul>{kid.accept.map((a) => <li key={a}>{a}</li>)}</ul> : null}
          </div>
        )}

        <div className="msec">进展</div>
        {!d && <div className="mempty">正在读…</div>}
        {d && evs.length === 0 && <div className="mempty">{kid.status === "waiting" ? "还没开工，等前面的人交了再做" : "还没动手"}</div>}
        <ol className="mlog">
          {evs.map((e) => {
            const p = (e.payload || {}) as P;
            const art = p.artifact_id ? state.artifacts.find((a) => a.id === p.artifact_id) : undefined;
            const cls = e.type === "error" || e.type === "verify_failed" ? "bad"
              : e.type === "tool_error" ? (kid.status === "done" ? "warn" : "bad")
                : e.type === "thought" ? "thought" : e.type === "verified" || e.type === "done" ? "ok"
                  : e.type === "rework" || e.type === "approval" || e.type === "denied" || e.type === "escalate" ? "warn" : "";
            return (
              <li key={e.id} className={cls}>
                <span className="tm">{hm(e.created_at)}</span>
                <div className="tx">
                  {art ? (
                    <button className="mfile" onClick={() => onOpenFile(art)}><span className={`fi sm ${fileTone(art.name, art.kind)}`}>{fileBadge(art.name, art.kind)}</span>{p.text}</button>
                  ) : p.output ? (
                    <button className="mstep" onClick={() => setOpen(open === e.id ? null : e.id)}>{p.text}<Icon name={open === e.id ? "chev" : "right"} /></button>
                  ) : (
                    <span>{p.text}</span>
                  )}
                  {open === e.id && p.output && <pre className="sel">{p.output}</pre>}
                  {p.problems?.length ? <ul>{p.problems.slice(0, 4).map((x) => <li key={x}>{x}</li>)}</ul> : null}
                </div>
              </li>
            );
          })}
        </ol>

        {team.length > 0 && (
          <>
            <div className="msec">跟队友说的</div>
            {team.map((t) => (
              <div key={t.id} className={`tm ${t.kind}`}>
                <Icon name={t.kind === "issue" ? "restart" : "chat"} />
                <b>{t.from} → {t.to || "全队"}</b>
                <span>{t.kind === "issue" ? "退回：" : ""}{t.text}</span>
              </div>
            ))}
          </>
        )}
        {files.length > 0 && (
          <>
            <div className="msec">交的文件</div>
            {files.map((a) => (
              <button key={a.id} className="mfile row" onClick={() => onOpenFile(a)}>
                <span className={`fi sm ${fileTone(a.name, a.kind)}`}>{fileBadge(a.name, a.kind)}</span>{a.name}
              </button>
            ))}
          </>
        )}
        {kid.result && !live && (
          <>
            <div className="msec">交代</div>
            <div className="mres sel"><Markdown text={kid.result} /></div>
          </>
        )}
      </div>
    </aside>
  );
}
