// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 右栏：进度（每件在做的事一张进度卡：队员、留言和退回、插话怎么理解的）和产物（按这件事分组，可按类型筛）。
// 对话里只说结果，过程都在这里看。
import { useState } from "react";
import { api } from "../api";
import type { AppState, Artifact, Job, Task, Worker } from "../types";
import { fileBadge, isLive, pct, size, statusText, when, fileTone } from "../util";
import { avatarOf } from "./Avatar";
import { Icon, noteIcon } from "./Icon";
import { JobCard, Receipt, type Ctx } from "./Messages";
import working from "../assets/working.webp";
import team from "../assets/team.webp";

interface Props {
  state: AppState;
  workers: Worker[];
  onRecords: () => void;
  onOpenFile: (a: Artifact) => void;
  onError: (m: string) => void;
  onChanged: () => void;
  onMember: (taskId: string) => void;
}

type Tab = "progress" | "files";
const KINDS: [string, string][] = [["", "全部"], ["doc", "文档"], ["sheet", "表格"], ["slides", "演示"]];

export function waitingFor(state: AppState, t: Task): boolean {
  return state.approvals.some((a) => a.task_id === t.id) ||
    state.decisions.some((d) => d.status === "pending_confirm" && d.target === t.id);
}

export function WorkLine({ t, state, workers, menuable, onError, onChanged }: {
  t: Task; state: AppState; workers: Worker[]; menuable?: boolean; onError: (m: string) => void; onChanged: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState<{ wid: string; progress: number } | null>(null);
  const w = workers.find((x) => x.id === t.worker_id);
  const hold = waitingFor(state, t);
  const ask = state.approvals.find((a) => a.task_id === t.id);
  const pill = hold ? ["w", "待确认"] : t.status === "waiting" ? ["x", "排队"] : ["", statusText(t)];
  const note = ask ? `等你允许：${ask.summary}` : t.status === "waiting" ? "等前面的做完" : t.note || "";
  const done = Math.floor(t.progress * 5);
  const canMove = menuable && ["running", "pending", "waiting", "failed"].includes(t.status) && !hold;

  async function assign(wid: string, ok = false) {
    setMenu(false);
    try {
      const r = await api<{ need_confirm?: boolean; progress?: number }>(`/api/tasks/${t.id}/assign`, { body: { worker_id: wid, confirm: ok } });
      if (r.need_confirm) return setConfirm({ wid, progress: r.progress || 0 });
      setConfirm(null);
      onChanged();
    } catch (e) {
      onError(`改派失败：${(e as Error).message}`);
    }
  }

  return (
    <div className="wk">
      <div className="h">
        <img src={avatarOf(t.worker_id, workers)} alt="" />
        <div>
          <div className="nm">
            {t.title}
          </div>
          <div className="now">
            {canMove ? (
              <button className="who" onClick={() => setMenu(!menu)} title="改派">
                {w?.name || "未派"} ▾
              </button>
            ) : (
              <span className="who">{w?.name || "未派"}</span>
            )}
            {note && (
              <>
                <Icon name={noteIcon(note)} size={13} />
                <span title={note}>{note}</span>
              </>
            )}
          </div>
        </div>
        <span className={`tg ${pill[0]}`}>{pill[1]}</span>
      </div>
      {isLive(t) && t.status !== "waiting" && (
        <div className={`steps ${hold ? "h" : ""}`} title={pct(t.progress)}>
          {[0, 1, 2, 3, 4].map((i) => <i key={i} className={i < done ? "d" : ""} />)}
        </div>
      )}
      {menu && (
        <div className="menu" role="menu">
          {t.assign_reason && <div className="why">{t.assign_reason}</div>}
          {workers.filter((x) => x.enabled && x.id !== t.worker_id).map((x) => (
            <button key={x.id} role="menuitem" onClick={() => assign(x.id)}>
              <img src={avatarOf(x.id, workers)} alt="" />
              {x.name}
            </button>
          ))}
        </div>
      )}
      {confirm && (
        <div className="confirm-line">
          已跑 {pct(confirm.progress)}，改派会丢弃
          <span className="sp" />
          <button className="btn sm" onClick={() => setConfirm(null)}>不改了</button>
          <button className="btn sm d" onClick={() => assign(confirm.wid, true)}>确认改派</button>
        </div>
      )}
    </div>
  );
}

export function FileRow({ a, workers, onOpen }: { a: Artifact; workers: Worker[]; onOpen: (a: Artifact) => void }) {
  return (
    <button className="af" onClick={() => onOpen(a)} title={a.path}>
      <span className={`fi sm ${fileTone(a.name, a.kind)}`}>{fileBadge(a.name, a.kind)}</span>
      <div>
        <div className="fn">{a.name}</div>
        <div className="fm">
          {a.worker_id && <img src={avatarOf(a.worker_id, workers)} alt="" />}
          {size(a.size)}
        </div>
      </div>
      <span className="tm">{when(a.created_at)}</span>
    </button>
  );
}

export function Rail({ state, workers, onRecords, onOpenFile, onError, onChanged, onMember }: Props) {
  const [tab, setTab] = useState<Tab>("progress");
  const [kind, setKind] = useState("");
  const live = state.jobs.filter((j) => isLive(j));
  const kids = live.flatMap((j) => j.children.filter((k) => k.status !== "cancelled" && isLive(k)));
  const files = state.artifacts.filter((a) => !kind || a.kind === kind);
  const ctx: Ctx = { state, workers, onOpenFile, onError, onChanged, onMember };
  const today = new Date().setHours(0, 0, 0, 0);
  const doneToday = state.jobs.filter((j) => !isLive(j) && j.updated_at >= today).length;
  const groups: [Job | undefined, Artifact[]][] = [];
  for (const a of files) {
    const j = state.jobs.find((x) => x.id === a.main_id);
    const g = groups.find(([gj]) => gj?.id === j?.id);
    if (g) g[1].push(a);
    else groups.push([j, [a]]);
  }

  return (
    <aside className="rail">
      <div className="rh" data-tauri-drag-region>
        <div className="tabs" role="tablist">
          <button className={tab === "progress" ? "on" : ""} onClick={() => setTab("progress")}>
            进度<span className="n">{live.length}</span>
          </button>
          <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
            产物<span className="n">{state.artifacts.length}</span>
          </button>
        </div>
        <span className="sp" data-tauri-drag-region />
      </div>
      {tab === "progress" ? (
        <div className="rb">
          {kids.length === 0 && live.every((j) => j.children.length > 0) && (
            <div className="blank">
              <img src={team} alt="" />
              队员都闲着，交给 Yip 一件事就会忙起来
            </div>
          )}
          {live.filter((j) => j.children.length > 0).map((j) => {
            const decs = state.decisions.filter((d) => d.task_id === j.id).slice(-2);
            const said = (id: number) => [...state.messages].reverse().find((m) => m.meta?.act === id)?.content;
            return (
              <div key={j.id} className="rjob">
                <JobCard job={j} ctx={ctx} />
                {decs.map((d, i) => (
                  <Receipt key={d.id} dec={d} job={j} said={said(d.id) || undefined} stale={i < decs.length - 1} ctx={ctx} />
                ))}
              </div>
            );
          })}
          {live.filter((j) => j.children.length === 0).map((j) => (
            <div className="wk" key={j.id}>
              <div className="h">
                <img src={working} alt="" style={{ borderRadius: 9 }} />
                <div>
                  <div className="nm">{j.title}</div>
                  <div className="now">{j.note === "等你点头" ? "等你看大纲，说开工就派人" : "正在拆解，马上派人"}</div>
                </div>
              </div>
            </div>
          ))}
          {doneToday > 0 && (
            <div className="rdone"><span>今天做完 {doneToday} 件</span><button className="lk" onClick={onRecords}>去记录里看 ›</button></div>
          )}
        </div>
      ) : (
        <div className="rb">
          <div className="flt">
            {KINDS.map(([k, label]) => (
              <button key={k} className={`chip ${kind === k ? "on" : ""}`} onClick={() => setKind(k)}>{label}</button>
            ))}
          </div>
          {groups.length === 0 && (
            <div className="blank">
              <Icon name="doc" size={28} />
              队员写出的文件会出现在这里
            </div>
          )}
          {groups.map(([j, list]) => (
            <div key={j?.id || "none"}>
              <div className="gt">{j?.title || "其他"}<span className="n">{list.length}</span></div>
              {list.map((a) => <FileRow key={a.id} a={a} workers={workers} onOpen={onOpenFile} />)}
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
