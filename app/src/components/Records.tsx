// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 记录页：做完的事，一件事一行，按天分组；能搜、能筛「交付了 / 没做成」。正在做的在对话页右栏「进度」里。
// 点一行展开：各块谁做的、结果怎样，交的文件点一下打开。
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Artifact, Task, Worker } from "../types";
import { isNote } from "../util";
import { avatarOf } from "./Avatar";
import { FileSheet } from "./FileSheet";
import { Icon } from "./Icon";
import { FileRow } from "./Rail";

interface Rec {
  id: string;
  title: string;
  status: "done" | "failed" | "cancelled";
  kind: string;
  note: string;
  updated_at: number;
  problems: number;
  workers: string[];
  files: number;
  first_file: string;
}
type Filter = "" | "done" | "failed";
const FILTERS: [Filter, string][] = [["", "全部"], ["done", "交付了"], ["failed", "没做成"]];

const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
function dayGroup(ms: number): string {
  const today = startOf(new Date());
  const diff = Math.round((today - startOf(new Date(ms))) / 86400000);
  if (diff <= 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff < 7) return "这周";
  const d = new Date(ms);
  return `${d.getFullYear()} 年 ${d.getMonth() + 1} 月`;
}
function time(ms: number): string {
  const d = new Date(ms);
  const diff = Math.round((startOf(new Date()) - startOf(d)) / 86400000);
  return diff <= 1 ? d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")}`;
}
function result(r: Rec): [string, string] {
  if (r.status === "done") return r.problems ? ["amber", `交了，还差 ${r.problems} 处`] : ["", "交付了"];
  return r.status === "failed" ? ["red", "没做成"] : ["grey", "取消了"];
}
function sub(r: Rec): string {
  if (r.status === "failed") return r.note.replace(/^出错了：/, "") || "中断了";
  return [r.kind === "auto" ? "自动化" : "", r.first_file].filter(Boolean).join(" · ");
}

export function Records({ workers }: { workers: Worker[] }) {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("");
  const [rows, setRows] = useState<Rec[] | null>(null);
  const [more, setMore] = useState(false);
  const [open, setOpen] = useState("");
  const [file, setFile] = useState<Artifact | null>(null);

  async function load(before = 0) {
    const params = new URLSearchParams({ q: q.trim(), status: filter, before: String(before) });
    const got = await api<Rec[]>(`/api/records?${params}`);
    setMore(got.length >= 40);
    setRows((r) => (before && r ? [...r, ...got] : got));
  }
  useEffect(() => {
    const t = window.setTimeout(() => { load().catch(() => setRows([])); }, q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [q, filter]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const out: [string, Rec[]][] = [];
    for (const r of rows || []) {
      const g = dayGroup(r.updated_at);
      const hit = out.find(([k]) => k === g);
      if (hit) hit[1].push(r);
      else out.push([g, [r]]);
    }
    return out;
  }, [rows]);

  return (
    <section className="page">
      <div className="ph" data-tauri-drag-region>
        <h1>记录</h1>
        <span className="sp" data-tauri-drag-region />
        <label className="rsearch">
          <Icon name="search" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜这件事、队员、文件" />
        </label>
        <div className="seg">
          {FILTERS.map(([f, label]) => <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{label}</button>)}
        </div>
      </div>
      <div className="pb">
        {rows === null && <div className="hempty">正在找…</div>}
        {rows?.length === 0 && (
          <div className="hempty">{q || filter ? "没找到" : "做完的事会记在这里，正在做的在对话页右栏「进度」里看"}</div>
        )}
        {groups.map(([g, list]) => (
          <div key={g}>
            <div className="rday">{g}</div>
            <div className="rlist">
              {list.map((r) => {
                const [cls, text] = result(r);
                return (
                  <div key={r.id} className={`rrow-wrap ${open === r.id ? "on" : ""}`}>
                    <button className="rrow" onClick={() => setOpen(open === r.id ? "" : r.id)} aria-expanded={open === r.id}>
                      <span className="t"><b>{r.title}</b><small>{sub(r)}</small></span>
                      <span className="av">{r.workers.slice(0, 4).map((w) => <img key={w} src={avatarOf(w, workers)} alt="" title={workers.find((x) => x.id === w)?.name} />)}</span>
                      <span><span className={`rtag ${cls}`}>{text}</span></span>
                      <span className="files">{r.files ? `${r.files} 个文件` : "—"}</span>
                      <span className="time">{time(r.updated_at)}</span>
                    </button>
                    {open === r.id && <Detail id={r.id} workers={workers} onFile={setFile} />}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        {rows && rows.length > 0 && more && (
          <button className="btn sm hmore" onClick={() => load(rows[rows.length - 1].updated_at)}>再往前看</button>
        )}
      </div>
      {file && <FileSheet a={file} onClose={() => setFile(null)} />}
    </section>
  );
}

interface Full {
  result: string | null;
  children: Task[];
  artifacts: Artifact[];
  messages: { role: string; content: string; meta?: { final?: boolean; files?: number[]; artifact_id?: number } }[];
}

/** 展开一行：交付时 Yip 说的那句话、各块谁做的、交的文件 */
function Detail({ id, workers, onFile }: { id: string; workers: Worker[]; onFile: (a: Artifact) => void }) {
  const [d, setD] = useState<Full | null>(null);
  useEffect(() => { api<Full>(`/api/tasks/${id}`).then(setD).catch(() => undefined); }, [id]);
  if (!d) return <div className="rdetail note">正在打开…</div>;
  const fin = [...d.messages].reverse().find((m) => m.role === "assistant" && m.meta?.final);
  const ids = fin ? [fin.meta?.artifact_id, ...(fin.meta?.files || [])].filter((x): x is number => typeof x === "number") : [];
  const files = ids.length ? d.artifacts.filter((a) => ids.includes(a.id)) : d.artifacts.filter((a) => !isNote(a.name, workers));
  const name = (wid: string | null) => workers.find((w) => w.id === wid)?.name || "未派";
  const st = (k: Task) => k.status === "done" ? (k.verdict && !k.verdict.pass ? "有问题没改完" : "交了") : k.status === "failed" ? (k.note || "中断了") : "取消了";
  return (
    <div className="rdetail">
      {fin && <p className="said">{fin.content}</p>}
      {d.children.map((k) => (
        <div key={k.id} className="rpart">
          <img src={avatarOf(k.worker_id, workers)} alt="" />
          <span className="rwho">{name(k.worker_id)}</span>
          <span className="rwhat">{k.title}</span>
          <span className={`rst ${k.status === "done" && !(k.verdict && !k.verdict.pass) ? "" : "bad"}`}>{st(k)}</span>
        </div>
      ))}
      {files.length > 0 && <div className="rfiles">{files.map((a) => <FileRow key={a.id} a={a} workers={workers} onOpen={onFile} />)}</div>}
    </div>
  );
}
