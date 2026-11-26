// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 队员页：按小队分组的卡片墙。新建（一句话 / 手动 / 导入）、详情（资料、头像和权限、试一下）、编辑、复制。
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Artifact, Connector, ExternalAgent, GlobalSkill, Report, TaskEvent, Worker, WorkerForm } from "../types";
import { AVATARS, AVATAR_IDS, avatarOf } from "./Avatar";
import { FileSheet } from "./FileSheet";
import { Icon, noteIcon } from "./Icon";
import { Markdown } from "./Markdown";
import { FileRow } from "./Rail";
import { Sheet } from "./Sheet";
import { Draft as SkillDraft, NewSkill } from "./Skills";

const SRC: Record<string, string> = { builtin: "内置", custom: "自建", imported: "导入", external: "外部" };

interface Props {
  workers: Worker[];
  configured: boolean;
  onChanged: () => Promise<void> | void;
}

type Tab = "info" | "prefs" | "try";
type Modal = { k: "view"; id: string; tab?: Tab } | { k: "draft" } | { k: "form"; init: WorkerForm; drafted?: boolean; editId?: string }
  | { k: "import"; report: Report | null; worker: Worker | null; error?: string } | { k: "ext"; id: string } | null;

export function Members({ workers, configured, onChanged }: Props) {
  const [modal, setModal] = useState<Modal>(null);
  const [note, setNote] = useState("");
  const [exts, setExts] = useState<ExternalAgent[]>([]);
  const file = useRef<HTMLInputElement>(null);

  const loadExts = () => api<ExternalAgent[]>("/api/external-agents").then(setExts).catch(() => undefined);
  useEffect(() => { loadExts(); }, []);

  async function importZip(f: File) {
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api<{ report: Report; worker: Worker | null }>("/api/workers/import", { method: "POST", body: fd });
      await onChanged();
      setModal({ k: "import", report: r.report, worker: r.worker });
    } catch (e) {
      setModal({ k: "import", report: null, worker: null, error: `导入失败：${(e as Error).message}` });
    }
  }
  async function enable(id: string, on: boolean) {
    await api(`/api/workers/${id}/enable`, { body: { enabled: on } });
    await onChanged();
  }
  const [checkingId, setCheckingId] = useState("");
  async function toggleExt(ext: ExternalAgent, on: boolean) {
    if (on) {
      setCheckingId(ext.id);
      setNote(`正在检查 ${ext.name} 连通性…`);
      try {
        const ck = await api<{ ok: boolean; message: string }>(`/api/external-agents/${ext.id}/check`, { body: {} });
        await loadExts();
        if (!ck.ok) {
          setNote("");
          setModal({ k: "ext", id: ext.id });
          return;
        }
      } catch {
        setNote("");
        setModal({ k: "ext", id: ext.id });
        return;
      } finally {
        setCheckingId("");
      }
    }
    await api(`/api/external-agents/${ext.id}`, { method: "PUT", body: { enabled: on } });
    await Promise.all([loadExts(), onChanged()]);
    setNote(on ? `检查通过，已上岗：${ext.name}` : `已停用：${ext.name}`);
  }

  const teams: [string, Worker[]][] = [];
  for (const w of workers) {
    if (w.source === "external") continue;
    const t = w.team || (w.source === "builtin" ? "内置队员" : "我建的队员");
    const g = teams.find(([n]) => n === t);
    if (g) g[1].push(w);
    else teams.push([t, [w]]);
  }
  const current = modal?.k === "view" ? workers.find((w) => w.id === modal.id) : undefined;
  const currentExt = modal?.k === "ext" ? exts.find((e) => e.id === modal.id) : undefined;

  return (
    <section className="page">
      <div className="ph" data-tauri-drag-region>
        <h1>队员</h1>
        <span className="note ok">{note}</span>
        <span className="sp" data-tauri-drag-region />
        <button className="btn p" disabled={!configured} title={configured ? "" : "先在设置里配模型"} onClick={() => setModal({ k: "draft" })}>一句话新建</button>
        <button className="btn" onClick={() => setModal({ k: "form", init: {} })}>手动新建</button>
        <button className="btn" onClick={() => file.current?.click()}>导入 zip</button>
        <input ref={file} type="file" accept=".zip" hidden onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) importZip(f);
        }} />
      </div>
      <div className="pb">
        {teams.map(([team, list]) => (
          <div key={team}>
            <div className="sec-t">{team}<span style={{ fontWeight: 400, marginLeft: 8 }}>{list.length} 位</span></div>
            <div className="cards">
              {list.map((w) => (
                <div key={w.id} className={`card click ${w.enabled ? "" : "off"}`} role="button" tabIndex={0}
                  onClick={() => setModal({ k: "view", id: w.id })} onKeyDown={(e) => e.key === "Enter" && setModal({ k: "view", id: w.id })}>
                  <div className="h">
                    <img src={avatarOf(w.id, workers)} alt="" />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <b>{w.name}</b>
                      <div className="note">{SRC[w.source]}{w.enabled ? "" : " · 没上岗"}</div>
                    </div>
                  </div>
                  <p>{w.introduction}</p>
                  <div className="chips">
                    {w.skills.map((s) => <span className="tag" key={s.id} title={s.description}>{s.name}</span>)}
                  </div>
                  {w.source !== "builtin" && (
                    <div className="acts" onClick={(e) => e.stopPropagation()}>
                      <button className={`btn sm ${w.enabled ? "" : "p"}`} onClick={() => enable(w.id, !w.enabled)}>{w.enabled ? "停用" : "上岗"}</button>
                      <button className="btn sm" onClick={async () => { await api(`/api/workers/${w.id}`, { method: "DELETE" }); await onChanged(); }}>删除</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
        {exts.length > 0 && (
          <div>
            <div className="sec-t">外部队员<span style={{ fontWeight: 400, marginLeft: 8 }}>{exts.length} 位</span></div>
            <div className="cards">
              {exts.map((ext) => {
                const ready = ext.status === "ready";
                const sub = !ready ? "暂未开发"
                  : ext.enabled ? (ext.checked_ok ? "已上岗 · 连通正常" : (ext.installed ? "已上岗 · 待检查" : "已上岗 · 未检测到命令"))
                  : (ext.installed ? "本机已就绪 · 没上岗" : "未安装");
                const busyOne = checkingId === ext.id;
                return (
                  <div key={ext.id} className={`card ${ready ? "click" : ""} ${ready && ext.enabled ? "" : "off"}`}
                    role={ready ? "button" : undefined} tabIndex={ready ? 0 : undefined}
                    onClick={() => ready && setModal({ k: "ext", id: ext.id })}
                    onKeyDown={(e) => ready && e.key === "Enter" && setModal({ k: "ext", id: ext.id })}>
                    <div className="h">
                      <img src={AVATARS[ext.avatar] || avatarOf(ext.id, workers)} alt="" />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <b>{ext.name}</b>
                        <div className="note">{sub}</div>
                      </div>
                    </div>
                    <p>{ext.introduction}</p>
                    <div className="chips">
                      <span className="tag">{ext.protocol.toUpperCase()}</span>
                      {ready ? (
                        <span className="tag">{ext.auth_mode === "api_key" ? "密钥模式" : "本机登录态"}</span>
                      ) : (
                        <span className="tag">暂未开发</span>
                      )}
                    </div>
                    {ready && (
                      <div className="acts" onClick={(e) => e.stopPropagation()}>
                        {ext.enabled ? (
                          <>
                            <button className="btn sm" onClick={() => toggleExt(ext, false)}>停用</button>
                            <button className="btn sm" onClick={() => setModal({ k: "ext", id: ext.id })}>配置</button>
                            <button className="btn sm" onClick={() => setModal({ k: "view", id: ext.id, tab: "try" })}>试一下</button>
                          </>
                        ) : ext.installed ? (
                          <>
                            <button className="btn sm p" disabled={busyOne} onClick={() => toggleExt(ext, true)}>
                              {busyOne ? "检查中…" : "检查并上岗"}
                            </button>
                            <button className="btn sm" onClick={() => setModal({ k: "ext", id: ext.id })}>配置</button>
                          </>
                        ) : (
                          <button className="btn sm p" onClick={() => setModal({ k: "ext", id: ext.id })}>安装与配置</button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      {modal?.k === "view" && current && (
        <MemberSheet w={current} workers={workers} tab={modal.tab} onClose={() => setModal(null)} onSaved={onChanged}
          onEdit={(form, editId) => setModal({ k: "form", init: form, editId })} />
      )}
      {modal?.k === "ext" && currentExt && (
        <ExternalSheet ext={currentExt} onClose={() => setModal(null)} onSaved={async (openTry) => {
          await Promise.all([loadExts(), onChanged()]);
          setNote(`已保存：${currentExt.name}`);
          if (openTry) setModal({ k: "view", id: currentExt.id, tab: "try" });
          else setModal(null);
        }} />
      )}
      {modal?.k === "draft" && <Draft onClose={() => setModal(null)} onDone={(init) => setModal({ k: "form", init, drafted: true })} />}
      {modal?.k === "form" && (
        <Form init={modal.init} drafted={modal.drafted} editId={modal.editId} configured={configured} onClose={() => setModal(null)} onSaved={async (w) => {
          await onChanged();
          setNote(modal.editId ? `已保存：${w.name}` : `已上岗：${w.name}`);
          setModal({ k: "view", id: w.id, tab: modal.editId ? "info" : "try" });
        }} />
      )}
      {modal?.k === "import" && (
        <Sheet title="导入队员" onClose={() => setModal(null)} foot={<>
          <span className="sp" />
          {modal.worker && <button className="btn p" onClick={async () => { await enable(modal.worker!.id, true); setModal({ k: "view", id: modal.worker!.id, tab: "try" }); }}>上岗并试一下</button>}
        </>}>
          {modal.error && <div className="report bad">{modal.error}</div>}
          {modal.report && <ReportBox r={modal.report} />}
          {modal.worker && <p>{modal.worker.name}：{modal.worker.introduction}</p>}
        </Sheet>
      )}
    </section>
  );
}

function ReportBox({ r }: { r: Report }) {
  return (
    <div className={`report ${r.ok ? "good" : "bad"}`}>
      {r.ok && <div>校验通过</div>}
      {r.errors.map((x) => <div key={x}>拒绝：{x}</div>)}
      {r.warns.map((x) => <div key={x} className="w">提示：{x}</div>)}
    </div>
  );
}

function ExternalSheet({ ext, onClose, onSaved }: {
  ext: ExternalAgent; onClose: () => void; onSaved: (openTry: boolean) => Promise<void>;
}) {
  const [auth, setAuth] = useState<"cli" | "api_key">(ext.auth_mode);
  const [key, setKey] = useState(ext.api_key);
  const [cmd, setCmd] = useState(ext.command);
  const [probe, setProbe] = useState({ installed: ext.installed, path: ext.path, reason: ext.probe_reason });
  const [check, setCheck] = useState<{ ok: boolean; msg: string } | null>(
    ext.check_msg ? { ok: ext.checked_ok, msg: ext.check_msg } : null,
  );
  const [installLog, setInstallLog] = useState("");
  const [busy, setBusy] = useState<"" | "probe" | "check" | "install" | "save">("");
  const [err, setErr] = useState("");

  async function recheck() {
    setBusy("probe");
    setErr("");
    try {
      const r = await api<{ installed: boolean; path: string; reason: string }>(`/api/external-agents/${ext.id}/probe`, {
        body: { command: cmd },
      });
      setProbe(r);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function runCheck(): Promise<boolean> {
    setBusy("check");
    setErr("");
    try {
      await api(`/api/external-agents/${ext.id}`, {
        method: "PUT",
        body: { auth_mode: auth, api_key: key, command: cmd },
      });
      const r = await api<{ ok: boolean; message: string; item: ExternalAgent }>(`/api/external-agents/${ext.id}/check`, {
        body: { command: cmd },
      });
      setProbe({ installed: r.item.installed, path: r.item.path, reason: r.item.probe_reason });
      setCheck({ ok: r.ok, msg: r.message });
      return r.ok;
    } catch (e) {
      const msg = (e as Error).message;
      setCheck({ ok: false, msg });
      return false;
    } finally {
      setBusy("");
    }
  }

  async function runInstall() {
    setBusy("install");
    setErr("");
    setInstallLog("正在安装，请等待…");
    try {
      const r = await api<{ ok: boolean; message: string; output: string; item: ExternalAgent }>(`/api/external-agents/${ext.id}/install`, {
        body: {},
      });
      setProbe({ installed: r.item.installed, path: r.item.path, reason: r.item.probe_reason });
      setInstallLog(r.output ? `${r.message}\n\n${r.output}` : r.message);
      if (!r.ok) setErr(r.message);
    } catch (e) {
      setErr((e as Error).message);
      setInstallLog("");
    } finally {
      setBusy("");
    }
  }

  async function save(enableAndTry: boolean) {
    setErr("");
    if (enableAndTry) {
      const ok = await runCheck();
      if (!ok) return;
    }
    setBusy("save");
    try {
      await api(`/api/external-agents/${ext.id}`, {
        method: "PUT",
        body: {
          auth_mode: auth,
          api_key: key,
          command: cmd,
          ...(enableAndTry ? { enabled: true } : {}),
        },
      });
      await onSaved(enableAndTry);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <Sheet title={`外部队员 · ${ext.name}`} onClose={onClose} foot={<>
      <span className="note bad">{err}</span><span className="sp" />
      <button className="btn" disabled={!!busy || !probe.installed} onClick={runCheck}>
        {busy === "check" ? "检查中…" : "测试连通"}
      </button>
      <button className="btn" disabled={!!busy} onClick={() => save(false)}>保存</button>
      <button className="btn p" disabled={!!busy || !probe.installed} onClick={() => save(true)}>
        {busy === "save" ? "上岗中…" : "上岗并试一下"}
      </button>
    </>}>
      <div className={`report ${probe.installed ? "good" : "bad"}`} style={{ marginBottom: 12 }}>
        <div>{probe.reason}{probe.path ? `（${probe.path}）` : ""}</div>
        {!probe.installed && <div className="w">{ext.install_hint}</div>}
      </div>
      {check && (
        <div className={`report ${check.ok ? "good" : "bad"}`} style={{ marginBottom: 12 }}>
          <div>{check.msg}</div>
        </div>
      )}
      {!probe.installed && ext.install_cmd && (
        <div className="field">
          <span>协助安装</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code className="sel" style={{ flex: 1, padding: "6px 8px", borderRadius: 8, background: "var(--bg)", fontSize: 12 }}>{ext.install_cmd}</code>
            <button type="button" className="btn sm p" disabled={!!busy} onClick={runInstall}>
              {busy === "install" ? "安装中…" : "一键安装"}
            </button>
          </div>
          {installLog && <pre className="sel" style={{ marginTop: 8, maxHeight: 140, overflow: "auto", fontSize: 11 }}>{installLog}</pre>}
        </div>
      )}
      <div className="field">
        <span>安全说明</span>
        <small className="note">{ext.safety_note}</small>
      </div>
      {ext.env_key && (
        <div className="field">
          <span>认证方式</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className={`btn sm ${auth === "api_key" ? "p" : ""}`} onClick={() => setAuth("api_key")}>密钥模式（推荐）</button>
            <button type="button" className={`btn sm ${auth === "cli" ? "p" : ""}`} onClick={() => setAuth("cli")}>本机官方登录态</button>
          </div>
        </div>
      )}
      {ext.env_key && auth === "api_key" && (
        <div className="field">
          <span>{ext.env_key}</span>
          <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="留空则沿用本机环境变量" />
        </div>
      )}
      <div className="field">
        <span>启动命令（可选）</span>
        <div style={{ display: "flex", gap: 8 }}>
          <input style={{ flex: 1 }} value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder="留空自动检测默认命令" />
          <button type="button" className="btn sm" disabled={!!busy} onClick={recheck}>重新检测</button>
        </div>
      </div>
    </Sheet>
  );
}

interface Detail {
  form: WorkerForm & { sample?: string };
  skill_docs: { id: string; name: string; description: string; doc: string }[];
}

/** 从技能库里勾技能；库里没有的当场起草一个，存好自动勾上 */
function SkillPicker({ value, onChange, hints = [], configured = true }: {
  value: string[]; onChange: (v: string[]) => void; hints?: string[]; configured?: boolean;
}) {
  const [skills, setSkills] = useState<GlobalSkill[]>([]);
  const [make, setMake] = useState<{ k: "draft"; text: string } | { k: "new"; init?: { id?: string; name: string; description: string; body: string } } | null>(null);
  const [left, setLeft] = useState(hints);
  const [hint, setHint] = useState("");
  const load = () => api<GlobalSkill[]>("/api/skills").then((r) => setSkills(r.filter((s) => s.source === "builtin" || s.source === "global")));
  useEffect(() => { load(); }, []);
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  return (
    <>
      <div className="pick">
        {skills.map((s) => (
          <button key={s.id} type="button" className={`pick-item ${value.includes(s.id) ? "on" : ""}`} onClick={() => toggle(s.id)} title={s.description}>
            <span className="box">{value.includes(s.id) && <Icon name="check" size={12} />}</span>
            <span><b>{s.name}</b><small>{s.description}</small></span>
          </button>
        ))}
      </div>
      <div className="pick-new">
        {left.map((h) => (
          <button key={h} type="button" className="btn sm" disabled={!configured} title="技能库里还没有，起草一个" onClick={() => { setHint(h); setMake({ k: "draft", text: h }); }}>
            <Icon name="plus" size={12} /> {h}
          </button>
        ))}
        <button type="button" className="btn sm" onClick={() => { setHint(""); setMake(configured ? { k: "draft", text: "" } : { k: "new" }); }}>新建技能</button>
      </div>
      {make?.k === "draft" && <SkillDraft text={make.text} onClose={() => setMake(null)} onDone={(init) => setMake({ k: "new", init })} />}
      {make?.k === "new" && <NewSkill init={make.init} onClose={() => setMake(null)} onSaved={async (n) => {
        setMake(null);
        setLeft(left.filter((h) => h !== hint));
        await load();
        onChange([...value.filter((x) => x !== n.id), n.id]);
      }} />}
    </>
  );
}

function ConnPicker({ value, onChange }: { value: string[] | null; onChange: (v: string[] | null) => void }) {
  const [conns, setConns] = useState<Connector[]>([]);
  useEffect(() => { api<Connector[]>("/api/mcp").then(setConns); }, []);
  if (!conns.length) return <small className="note">还没接连接器</small>;
  const all = conns.map((c) => c.name);
  const cur = value ?? all;
  const toggle = (id: string) => {
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    onChange(next.length === all.length ? null : next);
  };
  return (
    <div className="pick">
      {conns.map((c) => (
        <button key={c.name} type="button" className={`pick-item ${cur.includes(c.name) ? "on" : ""}`} onClick={() => toggle(c.name)}>
          <span className="box">{cur.includes(c.name) && <Icon name="check" size={12} />}</span>
          <span><b>{c.name}</b></span>
        </button>
      ))}
    </div>
  );
}

function AvatarPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="avatars">
      {AVATAR_IDS.map((id) => (
        <button key={id} type="button" className={value === id ? "on" : ""} onClick={() => onChange(id)}><img src={AVATARS[id]} alt="" /></button>
      ))}
    </div>
  );
}

function MemberSheet({ w, workers, tab: initTab, onClose, onSaved, onEdit }: {
  w: Worker; workers: Worker[]; tab?: Tab; onClose: () => void; onSaved: () => Promise<void> | void; onEdit: (f: WorkerForm, editId?: string) => void;
}) {
  const [d, setD] = useState<Detail | null>(null);
  const [tab, setTab] = useState<Tab>(initTab || "info");
  const [avatar, setAvatar] = useState(w.avatar || (AVATARS[w.id] ? w.id : ""));
  const [gs, setGs] = useState<string[]>(w.global_skills || []);
  const [cs, setCs] = useState<string[] | null>(w.connectors);
  const [note, setNote] = useState("");
  useEffect(() => { api<Detail>(`/api/workers/${w.id}`).then(setD); }, [w.id]);
  async function save() {
    await api(`/api/workers/${w.id}/prefs`, { method: "PUT", body: { avatar, global_skills: gs, connectors: cs } });
    await onSaved();
    setNote("已保存");
  }
  async function copy() {
    const r = await api<{ report: Report; worker: Worker | null }>(`/api/workers/${w.id}/copy`, { method: "POST" });
    if (!r.worker) return setNote(r.report.errors.join("；"));
    await onSaved();
    const nd = await api<Detail>(`/api/workers/${r.worker.id}`);
    onEdit(nd.form, r.worker.id);
  }
  const f = d?.form;
  return (
    <Sheet title={w.name} wide tall onClose={onClose}
      head={<div className="tabs">
        <button className={tab === "info" ? "on" : ""} onClick={() => setTab("info")}>资料</button>
        <button className={tab === "prefs" ? "on" : ""} onClick={() => setTab("prefs")}>头像和权限</button>
        <button className={tab === "try" ? "on" : ""} onClick={() => setTab("try")}>试一下</button>
      </div>}
      foot={tab === "try" ? undefined : <>
        <span className="note ok">{note}</span><span className="sp" />
        {tab === "prefs" ? <button className="btn p" onClick={save}>保存</button>
          : w.source === "custom" ? <button className="btn p" disabled={!f} onClick={() => f && onEdit({ ...f, avatar, global_skills: gs } as WorkerForm, w.id)}>编辑资料</button>
          : <button className="btn" onClick={copy} title="内置和导入的队员不能直接改">复制一份改</button>}
      </>}>
      {tab === "info" && (
        <>
          <div className="card" style={{ flexDirection: "row", alignItems: "center", gap: 14, marginBottom: 14 }}>
            <img src={avatarOf(w.id, workers)} alt="" style={{ width: 64, height: 64, borderRadius: "50%" }} />
            <div style={{ flex: 1 }}>
              <b style={{ fontSize: 16 }}>{w.name}</b>
              <div className="note">{w.team ? `${w.team} · ` : ""}{SRC[w.source]}{w.enabled ? "" : " · 没上岗"}</div>
              <p style={{ marginTop: 4 }}>{w.introduction}</p>
            </div>
          </div>
          <dl className="dl">
            <dt>个性</dt><dd><div className="chips">{w.personality.map((x) => <span className="tag" key={x}>{x}</span>)}</div></dd>
            <dt>要给它</dt><dd>{w.inputs || "—"}</dd>
            <dt>交付标准</dt><dd>{w.deliverable || "—"}</dd>
            {f?.identity && <><dt>身份</dt><dd className="sel">{f.identity}</dd></>}
          </dl>
          <div className="sec-t">技能 {d?.skill_docs.length ?? w.skills.length}</div>
          {(d?.skill_docs || []).map((k) => (
            <details className="sk" key={k.id}>
              <summary><b>{k.name}</b><small>{k.description}</small></summary>
              <Markdown text={k.doc.replace(/^---[\s\S]*?\n---\s*\n/, "")} />
            </details>
          ))}
        </>
      )}
      {tab === "prefs" && (
        <>
          <div className="field"><span>头像</span><AvatarPicker value={avatar} onChange={setAvatar} /></div>
          <div className="field"><span>技能</span><SkillPicker value={gs} onChange={setGs} /></div>
          <div className="field"><span>能用的连接器</span><ConnPicker value={cs} onChange={setCs} /></div>
        </>
      )}
      {tab === "try" && <Trial w={w} sample={f?.sample || ""} />}
    </Sheet>
  );
}

interface TrialJob {
  id: string;
  status: string;
  result: string | null;
  children: { id: string; status: string; note: string | null; result: string | null }[];
  events: TaskEvent[];
  artifacts: Artifact[];
  approvals: { id: number; tool: string; summary: string }[];
}

const SHOW = ["step", "tool_error", "approval", "approved", "denied", "done", "error", "constraint_applied"];

function Trial({ w, sample }: { w: Worker; sample: string }) {
  const [text, setText] = useState(sample);
  const [job, setJob] = useState<string | null>(null);
  const [data, setData] = useState<TrialJob | null>(null);
  const [file, setFile] = useState<Artifact | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (!text && sample) setText(sample); }, [sample]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!job) return;
    let alive = true;
    const tick = async () => {
      try {
        const r = await api<TrialJob>(`/api/tasks/${job}`);
        if (!alive) return;
        setData(r);
        if (!["done", "failed", "cancelled"].includes(r.status)) setTimeout(tick, 1200);
      } catch {
        /* 删掉了 */
      }
    };
    tick();
    return () => { alive = false; };
  }, [job]);
  async function start() {
    setErr("");
    setData(null);
    try {
      const r = await api<{ job: string }>(`/api/workers/${w.id}/try`, { body: { text } });
      setJob(r.job);
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function drop() {
    if (job) await api(`/api/trials/${job}`, { method: "DELETE" });
    setJob(null);
    setData(null);
  }
  const kid = data?.children[0];
  const done = data && ["done", "failed", "cancelled"].includes(data.status);
  const evs = (data?.events || []).filter((e) => SHOW.includes(e.type)).slice().reverse();
  return (
    <>
      <label className="field">
        <span>给它一件小事试试</span>
        <textarea className="input" value={text} onChange={(e) => setText(e.target.value)} disabled={!!job && !done} />
        <small>只派给 {w.name} 一个人做，不进对话和记忆；要写文件、跑命令时照常在输入框上方问你</small>
      </label>
      <div className="acts" style={{ marginBottom: 14 }}>
        {!job || done ? <button className="btn p" disabled={!text.trim()} onClick={start}>{job ? "再试一次" : "开始"}</button>
          : <span className="note"><span className="dots"><i /><i /><i /></span> {kid?.note || "正在开工"}</span>}
        {job && <button className="btn" onClick={drop}>{done ? "删掉这次试用" : "停下并删掉"}</button>}
        <span className="note bad">{err}</span>
      </div>
      {evs.length > 0 && (
        <div className="list" style={{ marginBottom: 14 }}>
          {evs.map((e) => (
            <div className="li" key={e.id} style={{ padding: "8px 14px" }}>
              <span className="ico" style={{ width: 26, height: 26 }}><Icon name={noteIcon(e.payload?.text || "")} size={14} /></span>
              <div className="tx"><div className="d" style={{ whiteSpace: "normal", color: e.type === "tool_error" || e.type === "error" ? "var(--danger)" : "var(--ink2)" }}>{e.payload?.text}</div></div>
            </div>
          ))}
        </div>
      )}
      {(data?.approvals || []).map((a) => (
        <div className="ti" key={a.id} style={{ marginBottom: 10 }}>
          <span className="k"><Icon name={noteIcon(a.summary)} size={14} /></span>
          <div className="tx"><div>{w.name} 想{a.tool === "mail_send" ? "发邮件" : "动手"}</div><div className="s"><span className="mono">{a.summary}</span></div></div>
          <button className="btn sm" onClick={async () => { await api(`/api/approvals/${a.id}`, { body: { decision: "deny" } }); }}>拒绝</button>
          <button className="btn sm p" onClick={async () => { await api(`/api/approvals/${a.id}`, { body: { decision: "allow" } }); }}>允许</button>
        </div>
      ))}
      {done && kid?.result && (
        <>
          <div className="sec-t">交付</div>
          <div className="card"><Markdown text={kid.result} /></div>
        </>
      )}
      {data && data.artifacts.length > 0 && (
        <>
          <div className="sec-t">写出的文件</div>
          {data.artifacts.map((a) => <FileRow key={a.id} a={a} workers={[w]} onOpen={setFile} />)}
        </>
      )}
      {file && <FileSheet a={file} onClose={() => setFile(null)} />}
    </>
  );
}

function Draft({ onClose, onDone }: { onClose: () => void; onDone: (f: WorkerForm) => void }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState({ cls: "", text: "" });
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    setNote({ cls: "", text: "正在起草…" });
    try {
      onDone(await api<WorkerForm>("/api/workers/draft", { body: { text: text.trim() } }));
    } catch (e) {
      setNote({ cls: "bad", text: `起草失败：${(e as Error).message}` });
      setBusy(false);
    }
  }
  return (
    <Sheet title="一句话新建队员" onClose={onClose} foot={<>
      <span className={`note ${note.cls}`}>{note.text}</span><span className="sp" />
      <button className="btn p" disabled={busy || !text.trim()} onClick={go}>起草</button>
    </>}>
      <label className="field">
        <span>想要一个什么样的队员</span>
        <textarea className="input" autoFocus value={text} onChange={(e) => setText(e.target.value)}
          placeholder="比如：审合同条款、标出风险的队员，不出法律意见" />
      </label>
    </Sheet>
  );
}

type Row = { id?: string; name?: string; description?: string; not_for?: string; key: number };
let seq = 0;
const rnd = () => AVATAR_IDS.filter((x) => !x.startsWith("generic"))[Math.floor(Math.random() * 8) + 4] || AVATAR_IDS[0];

function Form({ init, drafted, editId, configured = true, onClose, onSaved }: {
  init: WorkerForm & { avatar?: string; global_skills?: string[] | null; sample?: string }; drafted?: boolean; editId?: string;
  configured?: boolean; onClose: () => void; onSaved: (w: Worker) => void;
}) {
  const [f, setF] = useState({
    name: init.name || "", introduction: init.introduction || "",
    personality: (init.personality || []).join("，"), capability: init.capability || "",
    inputs: init.inputs || "", deliverable: init.deliverable || "", identity: init.identity || "", sample: init.sample || "",
  });
  const [avatar, setAvatar] = useState(init.avatar || rnd());
  const [gs, setGs] = useState<string[]>(init.global_skills || []);
  const [rows, setRows] = useState<Row[]>(() => (init.skills || []).map((s) => ({ ...s, key: seq++ })));
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  async function save() {
    const skills = rows.map(({ key: _k, ...r }) => ({ ...r, name: (r.name || "").trim(), description: (r.description || "").trim(), not_for: (r.not_for || "").trim() })).filter((r) => r.name);
    if (!f.name.trim()) return setReport({ ok: false, errors: ["要有名称"], warns: [] });
    if (!f.introduction.trim()) return setReport({ ok: false, errors: ["写一句它干什么"], warns: [] });
    setBusy(true);
    try {
      const body = { ...f, id: editId || init.id || "", avatar, global_skills: gs,
        personality: f.personality.split(/[，,、\s]+/).filter(Boolean), skills };
      const r = editId
        ? await api<{ report: Report; worker: Worker | null }>(`/api/workers/${editId}`, { method: "PUT", body })
        : await api<{ report: Report; worker: Worker | null }>("/api/workers", { body });
      if (r.report.ok && r.worker) return onSaved(r.worker);
      setReport(r.report);
    } catch (e) {
      setReport({ ok: false, errors: [(e as Error).message], warns: [] });
    }
    setBusy(false);
  }
  return (
    <Sheet title={editId ? `编辑 ${init.name || ""}` : drafted ? "确认新队员" : "新建队员"} wide tall onClose={onClose}
      foot={<><span className="sp" /><button className="btn p" disabled={busy} onClick={save}>{editId ? "保存" : "保存并试一下"}</button></>}>
      {drafted && !report && <div className="report good">起草好了，改完保存</div>}
      {report && <ReportBox r={report} />}
      <div className="field"><span>头像</span><AvatarPicker value={avatar} onChange={setAvatar} /></div>
      <div className="grid2">
        <label className="field"><span>名称</span><input className="input" value={f.name} onChange={set("name")} autoFocus={!drafted && !editId} placeholder="比如：合同审查员" /></label>
        <label className="field"><span>个性</span><input className="input" value={f.personality} onChange={set("personality")} placeholder="3–5 个，用逗号隔开" /></label>
      </div>
      <label className="field"><span>它干什么、不干什么</span><textarea className="input" maxLength={200} value={f.introduction} onChange={set("introduction")} /></label>
      <div className="field">
        <span>技能</span>
        <SkillPicker value={gs} onChange={setGs} hints={init.missing} configured={configured} />
        {rows.length > 0 && (
          <div className="chips" style={{ marginTop: 8 }}>
            {rows.map((r) => (
              <span className="tag" key={r.key} title={r.description}>{r.name}（包里带的）
                <button type="button" className="icon-btn" aria-label={`去掉 ${r.name}`} onClick={() => setRows(rows.filter((x) => x.key !== r.key))}><Icon name="x" size={12} /></button>
              </span>
            ))}
          </div>
        )}
      </div>
      <details className="sk" open={!!editId}>
        <summary><b>更多</b><small>身份说明、要给它什么、交付标准、试用的一句话</small></summary>
        <label className="field" style={{ marginTop: 10 }}><span>身份说明</span><textarea className="input" value={f.identity} onChange={set("identity")} placeholder="第二人称，写清服务谁、边界在哪；不填按上面那句话生成" /></label>
        <div className="grid2">
          <label className="field"><span>要给它什么</span><input className="input" value={f.inputs} onChange={set("inputs")} /></label>
          <label className="field"><span>交付标准</span><input className="input" value={f.deliverable} onChange={set("deliverable")} /></label>
        </div>
        <label className="field"><span>试用的一句话</span><input className="input" value={f.sample} onChange={set("sample")} placeholder="建好后先拿它试，比如：审一下这份合同的违约条款" /></label>
      </details>
    </Sheet>
  );
}
