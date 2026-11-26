// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 聊天记录（照微信）：清屏前后的话都在这里。按关键词搜，按文件、图片、链接、日期筛；文件点一下在右栏打开。
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, rawUrl, viewUrl } from "../api";
import type { Artifact } from "../types";
import { fileBadge, size, fileTone } from "../util";
import { Icon } from "./Icon";
import { Sheet } from "./Sheet";
import icon from "../assets/icon.webp";

type Doc = Partial<Artifact> & { id: number; name: string; kind?: string; path?: string; size?: number };
type Row = { id: number; role: "user" | "assistant"; content: string; created_at: number; docs: Doc[]; images: Doc[]; links: string[] };
type Kind = "" | "file" | "image" | "link";
const KINDS: [Kind, string][] = [["", "全部"], ["file", "文件"], ["image", "图片"], ["link", "链接"]];
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dayTitle = (ms: number) => new Date(ms).toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" });
const hm = (ms: number) => new Date(ms).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

function Calendar({ value, onPick, onClose }: { value: string; onPick: (d: string) => void; onClose: () => void }) {
  const [cur, setCur] = useState(() => (value ? new Date(value) : new Date()));
  const [has, setHas] = useState<string[]>([]);
  const month = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}`;
  useEffect(() => { api<string[]>(`/api/history/dates?month=${month}`).then(setHas).catch(() => setHas([])); }, [month]);
  const first = new Date(cur.getFullYear(), cur.getMonth(), 1);
  const lead = (first.getDay() + 6) % 7;  // 周一开头
  const days = new Date(cur.getFullYear(), cur.getMonth() + 1, 0).getDate();
  const cells = [...Array(lead).fill(0), ...Array.from({ length: days }, (_, i) => i + 1)];
  return (
    <div className="hcal" role="dialog" aria-label="选日期">
      <div className="hcal-h">
        <button className="icon-btn" onClick={() => setCur(new Date(cur.getFullYear(), cur.getMonth() - 1, 1))} aria-label="上个月"><Icon name="back" /></button>
        <b>{cur.getFullYear()} 年 {cur.getMonth() + 1} 月</b>
        <button className="icon-btn" onClick={() => setCur(new Date(cur.getFullYear(), cur.getMonth() + 1, 1))} aria-label="下个月"><Icon name="right" /></button>
      </div>
      <div className="hcal-g">
        {["一", "二", "三", "四", "五", "六", "日"].map((w) => <span key={w} className="w">{w}</span>)}
        {cells.map((d, i) => {
          if (!d) return <span key={`e${i}`} />;
          const key = ymd(new Date(cur.getFullYear(), cur.getMonth(), d));
          const on = has.includes(key);
          return (
            <button key={key} className={`${key === value ? "sel" : ""}`} disabled={!on} onClick={() => { onPick(key); onClose(); }}>{d}</button>
          );
        })}
      </div>
    </div>
  );
}

export function History({ name = "Yip", onClose, onOpenFile }: { name?: string; onClose: () => void; onOpenFile: (a: Artifact) => void }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<Kind>("");
  const [date, setDate] = useState("");
  const [cal, setCal] = useState(false);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [more, setMore] = useState(true);
  const list = useRef<HTMLDivElement>(null);
  const keep = useRef<number | null>(null);  // 往前翻时记住离底部多远，加载完滚回原处

  async function load(before = 0) {
    const params = new URLSearchParams({ q: q.trim(), kind, date, before: String(before) });
    const got = await api<Row[]>(`/api/history?${params}`);  // 新的在前
    setMore(got.length >= 60);
    const el = list.current;
    keep.current = before && el ? el.scrollHeight - el.scrollTop : 0;
    setRows((r) => (before && r ? [...r, ...got] : got));
  }
  useLayoutEffect(() => {  // 照微信：从最新的看起，往上翻是更早的
    const el = list.current;
    if (!el || rows === null) return;
    el.scrollTop = keep.current ? el.scrollHeight - keep.current : el.scrollHeight;
  }, [rows]);
  useEffect(() => {
    const t = window.setTimeout(() => { load().catch(() => setRows([])); }, q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [q, kind, date]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const out: [string, Row[]][] = [];
    for (const r of [...(rows || [])].reverse()) {
      const d = dayTitle(r.created_at);
      const g = out.find(([k]) => k === d);
      if (g) g[1].push(r);
      else out.push([d, [r]]);
    }
    return out;
  }, [rows]);

  return (
    <Sheet title="聊天记录" onClose={onClose} wide tall flush>
      <div className="hist">
        <div className="hsearch">
          {date && <span className="hchip"><Icon name="clock" />{date}<button onClick={() => setDate("")} aria-label="去掉日期"><Icon name="x" size={11} /></button></span>}
          <Icon name="search" />
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索" />
        </div>
        <div className="htabs">
          {KINDS.map(([k, label]) => <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{label}</button>)}
          <button className={date ? "on" : ""} onClick={() => setCal(!cal)}>日期</button>
        </div>
        {cal && <Calendar value={date} onPick={setDate} onClose={() => setCal(false)} />}
        <div className="hlist" ref={list}>
          {rows === null && <div className="hempty">正在找…</div>}
          {rows?.length === 0 && <div className="hempty">没找到</div>}
          {rows && rows.length > 0 && more && (
            <button className="btn sm hmore" onClick={() => load(rows[rows.length - 1].id)}>再往前看</button>
          )}
          {groups.map(([day, list]) => (
            <div key={day}>
              <div className="hday">{day}</div>
              {list.map((r) => (
                <div key={r.id} className="hrow">
                  {r.role === "assistant" ? <img className="hav" src={icon} alt="" /> : <span className="hav me"><Icon name="user" size={16} /></span>}
                  <div className="hbody">
                    <div className="hmeta"><b>{r.role === "assistant" ? name : "我"}</b><span>{hm(r.created_at)}</span></div>
                    {r.content && <div className="htext sel">{r.content.replace(/\n{2,}/g, "\n").trim()}</div>}
                    {r.images.length > 0 && (
                      <div className="himgs">
                        {r.images.map((m) => (
                          <img key={`${m.id}${m.name}`} alt={m.name} title={m.name}
                            src={m.path ? viewUrl(m.id, m.name) : rawUrl(`/api/uploads/${m.id}/raw`)} />
                        ))}
                      </div>
                    )}
                    {r.docs.map((f) => (
                      <button key={`${f.id}${f.name}`} className="hfile" disabled={!f.path} onClick={() => f.path && onOpenFile(f as Artifact)}>
                        <span className={`fi sm ${fileTone(f.name, f.kind)}`}>{fileBadge(f.name, f.kind || "other")}</span>
                        <span>{f.name}{f.size ? ` · ${size(f.size)}` : ""}</span>
                      </button>
                    ))}
                    {kind === "link" && r.links.map((l) => <a key={l} className="hlink" href={l} target="_blank" rel="noreferrer">{l}</a>)}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </Sheet>
  );
}
