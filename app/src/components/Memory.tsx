// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 记忆页：Yip 记得的事。偏好和关于你每次都会带上；结论、经验、进展按相关度挑着用。
import { useEffect, useState } from "react";
import { api } from "../api";
import type { MemoryItem } from "../types";
import { when } from "../util";
import { Icon } from "./Icon";
import { Sheet } from "./Sheet";

const KINDS: [MemoryItem["kind"], string, string][] = [
  ["persona", "关于 Yip", "你给我起的名字、要我怎么说话"],
  ["preference", "偏好", "你希望我以后怎么做，每次都会照着做"],
  ["fact", "关于你", "你是谁、在做什么，每次都会带上"],
  ["lesson", "经验", "哪条路试过不行，下次绕开"],
  ["conclusion", "结论", "某件事定下的结论"],
  ["state", "进展", "做到哪一步、卡在哪"],
];
const GROUPS: { id: string; title: string; hint: string; kinds: MemoryItem["kind"][] }[] = [
  { id: "about", title: "关于你与偏好", hint: "每次对话都会带上", kinds: ["fact", "preference"] },
  { id: "active", title: "正在投入的事", hint: "阶段进展与定下的结论", kinds: ["state", "conclusion"] },
  { id: "rules", title: "经验与说话规矩", hint: "关于 Yip 的约定和踩过的坑", kinds: ["persona", "lesson"] },
];
const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map(([k, l]) => [k, l]));
const SRC: Record<string, string> = { manual: "你教的", chat: "聊天里记的", job: "做事时记的" };

export function Memory() {
  const [list, setList] = useState<MemoryItem[]>([]);
  const [kind, setKind] = useState<MemoryItem["kind"]>("preference");
  const [text, setText] = useState("");
  const [edit, setEdit] = useState<{ id: number; text: string } | null>(null);
  const [impOpen, setImpOpen] = useState(false);
  const [impText, setImpText] = useState("");
  const [impBusy, setImpBusy] = useState(false);
  const [impNote, setImpNote] = useState("");
  const load = () => api<MemoryItem[]>("/api/memory").then(setList);
  useEffect(() => { load(); }, []);

  async function add() {
    if (!text.trim()) return;
    await api("/api/memory", { body: { kind, content: text.trim() } });
    setText("");
    load();
  }
  async function save() {
    if (!edit) return;
    await api(`/api/memory/${edit.id}`, { method: "PUT", body: { content: edit.text } });
    setEdit(null);
    load();
  }
  async function doImport() {
    if (!impText.trim()) return;
    setImpBusy(true);
    setImpNote("");
    try {
      const r = await api<{ added: number }>("/api/memory/import", { body: { text: impText } });
      setImpOpen(false);
      setImpText("");
      setImpNote(`已导入 ${r.added} 条`);
      load();
    } catch (e) {
      setImpNote((e as Error).message);
    } finally {
      setImpBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="ph" data-tauri-drag-region>
        <h1>记忆</h1>
        <span className="note">{list.length} 条{impNote ? ` · ${impNote}` : ""}</span>
        <span className="sp" data-tauri-drag-region />
        <button className="btn" onClick={() => setImpOpen(true)}>导入资料</button>
      </div>
      <div className="pb">
        <div className="cbox" style={{ maxWidth: "none", margin: "0 0 18px", boxShadow: "var(--shadow)" }}>
          <textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="教 Yip 一件以后要记得的事，比如：给领导的材料先结论后论据"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); add(); } }} />
          <div className="row">
            <div className="seg big">
              {KINDS.slice(0, 4).map(([k, label]) => <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{label}</button>)}
            </div>
            <span className="sp" />
            <button className="go" disabled={!text.trim()} onClick={add} aria-label="记下"><Icon name="plus" /></button>
          </div>
        </div>
        {list.length === 0 && (
          <div className="list"><div className="li"><span className="ico"><Icon name="brain" /></span>
            <div className="tx"><b>还没有记忆</b><div className="d">在对话里说「记住：…」，或点右上角「导入资料」把个人简介一次带进来</div></div></div></div>
        )}
        {GROUPS.map((g) => {
          const rows = list.filter((m) => g.kinds.includes(m.kind));
          if (!rows.length) return null;
          return (
            <div key={g.id}>
              <div className="sec-t">{g.title}<span style={{ fontWeight: 400, marginLeft: 8 }}>{g.hint}</span></div>
              <div className="list">
                {rows.map((m) => (
                  <div className="mem-row" key={m.id}>
                    <div className="c">
                      {edit?.id === m.id ? (
                        <input className="input" autoFocus value={edit.text} onChange={(e) => setEdit({ id: m.id, text: e.target.value })}
                          onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) save(); if (e.key === "Escape") { e.preventDefault(); setEdit(null); } }} />
                      ) : (
                        <span className="sel">{m.content}</span>
                      )}
                      <small>{KIND_LABEL[m.kind] || m.kind} · {SRC[m.source] || "做事时记的"} · {when(m.updated_at || m.created_at)}{m.used_at ? ` · 最近用过 ${when(m.used_at)}` : ""}</small>
                    </div>
                    <div className="ops">
                      {edit?.id === m.id ? (
                        <button className="btn sm p" onClick={save}>保存</button>
                      ) : (
                        <button className="icon-btn" aria-label="改" onClick={() => setEdit({ id: m.id, text: m.content })}><Icon name="edit" size={15} /></button>
                      )}
                      <button className="icon-btn" aria-label="删掉" onClick={async () => { await api(`/api/memory/${m.id}`, { method: "DELETE" }); load(); }}><Icon name="trash" size={15} /></button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {impOpen && (
        <Sheet title="导入我的资料" wide onClose={() => setImpOpen(false)} foot={<>
          <span className="note">{impNote}</span>
          <span className="sp" />
          <label className="btn">
            选本地文件
            <input type="file" accept=".md,.txt,.markdown" style={{ display: "none" }} onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f) setImpText(await f.text());
            }} />
          </label>
          <button className="btn p" disabled={impBusy || !impText.trim()} onClick={doImport}>{impBusy ? "正在整理…" : "导入记忆库"}</button>
        </>}>
          <label className="field">
            <span>贴一段个人简介或工作偏好（支持 Markdown）</span>
            <textarea className="input" rows={10} value={impText} onChange={(e) => setImpText(e.target.value)}
              placeholder={"## 关于我\n- 我负责企业智能体产品\n\n## 偏好\n- 给领导的材料先结论后论据"} />
          </label>
        </Sheet>
      )}
    </section>
  );
}
