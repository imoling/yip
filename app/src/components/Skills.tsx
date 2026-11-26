// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 技能页：技能库分内置和我装的，导入的队员包里带的另列。
// 装：本机文件夹、zip、GitHub 链接；建：一句话起草 / 手动填；内置的复制一份才能改。
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { GlobalSkill, Worker } from "../types";
import { avatarOf } from "./Avatar";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { Sheet } from "./Sheet";

type Init = { id?: string; name: string; description: string; body: string };
type Modal = { k: "new"; init?: Init } | { k: "draft" } | { k: "install" } | { k: "view"; s: GlobalSkill } | null;
const FROM: Record<string, string> = { builtin: "内置", global: "我装的" };

export function Skills({ workers, configured }: { workers: Worker[]; configured: boolean }) {
  const [list, setList] = useState<GlobalSkill[]>([]);
  const [modal, setModal] = useState<Modal>(null);
  const [note, setNote] = useState({ cls: "", text: "" });
  const load = () => api<GlobalSkill[]>("/api/skills").then(setList);
  useEffect(() => { load(); }, []);
  const builtin = list.filter((s) => s.source === "builtin");
  const mine = list.filter((s) => s.source === "global");
  const own = list.filter((s) => !FROM[s.source]);

  const card = (s: GlobalSkill) => {
    const us = workers.filter((w) => s.users?.includes(w.id));
    return (
      <div className="card click skcard" key={`${s.source}-${s.id}`} onClick={() => setModal({ k: "view", s })} role="button" tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && setModal({ k: "view", s })}>
        <div className="h">
          <span className="logo-tile"><Icon name={s.has_script ? "terminal" : "skill"} size={22} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b>{s.name}</b>
            {(!FROM[s.source] || s.has_script) && <div className="note">{[FROM[s.source] ? "" : `${s.owner} 包里带的`, s.has_script ? "带脚本" : ""].filter(Boolean).join(" · ")}</div>}
          </div>
        </div>
        <p title={s.description}>{s.description}</p>
        <div className="who-stack">
          {us.slice(0, 5).map((w) => <img key={w.id} src={avatarOf(w.id, workers)} alt="" title={w.name} />)}
          <span>{us.length ? `${us.length} 位队员在用` : "还没有队员用，去队员那里勾上"}</span>
        </div>
      </div>
    );
  };

  return (
    <section className="page">
      <div className="ph" data-tauri-drag-region>
        <h1>技能</h1>
        <span className={`note ${note.cls}`}>{note.text}</span>
        <span className="sp" data-tauri-drag-region />
        <button className="btn p" disabled={!configured} title={configured ? "" : "先在设置里配模型"} onClick={() => setModal({ k: "draft" })}>一句话新建</button>
        <button className="btn" onClick={() => setModal({ k: "new" })}>手动新建</button>
        <button className="btn" onClick={() => setModal({ k: "install" })}>装技能</button>
      </div>
      <div className="pb">
        <div className="sec-t">我装的</div>
        {mine.length === 0 ? (
          <div className="empty-sk">
            <span className="logo-tile"><Icon name="skill" size={22} /></span>
            <b>还没装自己的技能</b>
            <ul>
              <li><Icon name="folder" size={14} />Claude Code、Hermes 的技能文件夹直接装</li>
              <li><Icon name="link" size={14} />GitHub 链接、zip 也行</li>
              <li><Icon name="chat" size={14} />或者跟 Yip 说「把 ~/.claude/skills 里的 xx 装上」</li>
            </ul>
            <div className="acts">
              <button className="btn sm p" onClick={() => setModal({ k: "install" })}>装技能</button>
              <button className="btn sm" disabled={!configured} onClick={() => setModal({ k: "draft" })}>一句话新建</button>
            </div>
          </div>
        ) : <div className="cards">{mine.map(card)}</div>}
        <div className="sec-t">内置</div>
        <div className="cards">{builtin.map(card)}</div>
        {own.length > 0 && <><div className="sec-t">队员包里带的</div><div className="cards">{own.map(card)}</div></>}
      </div>
      {modal?.k === "install" && <Install onClose={() => setModal(null)} onDone={(names) => { setModal(null); setNote({ cls: "ok", text: `已装好：${names.join("、")}` }); load(); }} />}
      {modal?.k === "draft" && <Draft onClose={() => setModal(null)} onDone={(init) => setModal({ k: "new", init })} />}
      {modal?.k === "new" && <NewSkill init={modal.init} onClose={() => setModal(null)} onSaved={(s) => { setModal(null); setNote({ cls: "ok", text: `已加入技能库：${s.name}` }); load(); }} />}
      {modal?.k === "view" && <ViewSkill s={modal.s} onClose={() => setModal(null)} onChanged={load} />}
    </section>
  );
}

function Install({ onClose, onDone }: { onClose: () => void; onDone: (names: string[]) => void }) {
  const [src, setSrc] = useState("");
  const [replace, setReplace] = useState(false);
  const [note, setNote] = useState({ cls: "", text: "" });
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  async function go() {
    setBusy(true);
    setNote({ cls: "", text: "正在装…" });
    try {
      const r = await api<{ skills: string[] }>("/api/skills/install", { body: { source: src.trim(), replace } });
      onDone(r.skills);
    } catch (e) {
      setNote({ cls: "bad", text: `没装上：${(e as Error).message}` });
      setBusy(false);
    }
  }
  async function zip(f: File) {
    const fd = new FormData();
    fd.append("file", f);
    const r = await api<{ ok: boolean; errors?: string[]; skills?: string[] }>("/api/skills/import", { method: "POST", body: fd });
    if (r.ok) onDone(r.skills!);
    else setNote({ cls: "bad", text: `没装上：${r.errors!.join("；")}` });
  }
  return (
    <Sheet title="装技能" onClose={onClose} foot={<>
      <span className={`note ${note.cls}`}>{note.text}</span><span className="sp" />
      <button className="btn" onClick={() => file.current?.click()}>选 zip</button>
      <input ref={file} type="file" accept=".zip" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) zip(f); }} />
      <button className="btn p" disabled={busy || !src.trim()} onClick={go}>装上</button>
    </>}>
      <label className="field">
        <span>文件夹或 GitHub 链接</span>
        <input className="input mono" autoFocus value={src} onChange={(e) => setSrc(e.target.value)} onKeyDown={(e) => e.key === "Enter" && src.trim() && go()}
          placeholder="~/.claude/skills/humanizer-zh 或 https://github.com/…" />
        <small>文件夹里有好几个技能时一起装</small>
      </label>
      <label className="check"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />装过的换成新的</label>
    </Sheet>
  );
}

export function Draft({ text: initText = "", onClose, onDone }: { text?: string; onClose: () => void; onDone: (f: Init) => void }) {
  const [text, setText] = useState(initText);
  const [note, setNote] = useState({ cls: "", text: "" });
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    setNote({ cls: "", text: "正在起草…" });
    try {
      onDone(await api("/api/skills/draft", { body: { text: text.trim() } }));
    } catch (e) {
      setNote({ cls: "bad", text: `起草失败：${(e as Error).message}` });
      setBusy(false);
    }
  }
  return (
    <Sheet title="一句话新建技能" onClose={onClose} foot={<>
      <span className={`note ${note.cls}`}>{note.text}</span><span className="sp" />
      <button className="btn p" disabled={busy || !text.trim()} onClick={go}>起草</button>
    </>}>
      <label className="field">
        <span>想让队员学会什么</span>
        <textarea className="input" autoFocus value={text} onChange={(e) => setText(e.target.value)}
          placeholder="比如：把招标文件里的资质要求、评分细则、废标条款摘出来，做成核对清单" />
      </label>
    </Sheet>
  );
}

export function NewSkill({ init, onClose, onSaved }: { init?: Init; onClose: () => void; onSaved: (s: { id: string; name: string }) => void }) {
  const [f, setF] = useState<Init>(init || { name: "", description: "", body: "" });
  const [err, setErr] = useState("");
  async function save() {
    try {
      onSaved(await api<{ id: string; name: string }>("/api/skills", { body: f }));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  return (
    <Sheet title={init ? "确认新技能" : "手动新建技能"} wide onClose={onClose}
      foot={<><span className="note bad">{err}</span><span className="sp" /><button className="btn p" disabled={!f.name.trim() || !f.description.trim()} onClick={save}>保存</button></>}>
      {init && <div className="report good">起草好了，改完保存</div>}
      <div className="grid2">
        <label className="field"><span>名称</span><input className="input" autoFocus={!init} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field"><span>什么时候用</span><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
          <small>队员靠这句判断要不要用，写具体</small></label>
      </div>
      <label className="field">
        <span>怎么做</span>
        <textarea className="input code" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })}
          placeholder={"## 怎么做\n1. web_search 搜…\n2. web_fetch 打开…\n\n## 交付\n- write_file 写成 Markdown…\n\n## 交付前自查\n- …"} />
      </label>
    </Sheet>
  );
}

function ViewSkill({ s, onClose, onChanged }: { s: GlobalSkill; onClose: () => void; onChanged: () => void }) {
  const [doc, setDoc] = useState<string | null>(null);
  const [editable, setEditable] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    api<{ doc: string; editable: boolean }>(`/api/skills/doc?source=${encodeURIComponent(s.source)}&id=${encodeURIComponent(s.id)}`)
      .then((r) => { setDoc(r.doc); setEditable(r.editable); setDraft(r.doc); });
  }, [s]);
  const body = (doc || "").replace(/^---[\s\S]*?\n---\s*\n/, "");
  async function save() {
    try {
      await api(`/api/skills/${encodeURIComponent(s.id)}`, { method: "PUT", body: { doc: draft } });
      setDoc(draft);
      setEditing(false);
      setErr("");
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function copy() {
    try {
      await api(`/api/skills/${encodeURIComponent(s.id)}/copy`, { method: "POST" });
      onChanged();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  return (
    <Sheet title={s.name} wide onClose={onClose} head={editable && !editing ? <button className="btn sm" onClick={() => setEditing(true)}>编辑</button> : undefined}
      foot={s.source === "builtin" ? <><span className="note bad">{err}</span><span className="sp" /><button className="btn" onClick={copy}>复制一份改</button></>
        : editable ? <>
        <span className="note bad">{err}</span><span className="sp" />
        {editing ? <>
          <button className="btn" onClick={() => { setEditing(false); setDraft(doc || ""); }}>取消</button>
          <button className="btn p" onClick={save}>保存</button>
        </> : <button className="btn" onClick={async () => { await api(`/api/skills/${s.id}`, { method: "DELETE" }); onChanged(); onClose(); }}>删除</button>}
      </> : undefined}>
      <dl className="dl">
        <dt>什么时候用</dt><dd>{s.description}</dd>
        <dt>来自</dt><dd>{FROM[s.source] || `${s.owner} 包里带的`}</dd>
        <dt>位置</dt><dd className="mono sel">{s.dir}</dd>
      </dl>
      {doc === null ? <div className="note">正在打开…</div>
        : editing ? <textarea className="input code" value={draft} onChange={(e) => setDraft(e.target.value)} />
        : <Markdown text={body} />}
    </Sheet>
  );
}
