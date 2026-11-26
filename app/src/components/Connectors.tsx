// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 连接器页：MCP 服务的列表、开关、要不要先问、测试；新建用表单或直接粘贴 mcpServers JSON。
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Connector, McpPreset } from "../types";
import { Icon } from "./Icon";
import { ConnectorLogo } from "./Logo";
import { Sheet } from "./Sheet";

export function Connectors({ onChanged }: { onChanged: () => void }) {
  const [list, setList] = useState<Connector[]>([]);
  const [edit, setEdit] = useState<Connector | "new" | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; text: string }>>({});
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [preset, setPreset] = useState<McpPreset | null>(null);
  const [view, setView] = useState<Connector | null>(null);
  const load = () => {
    api<Connector[]>("/api/mcp").then(setList);
    api<McpPreset[]>("/api/mcp/presets").then(setPresets);
  };
  useEffect(() => { load(); }, []);

  async function patch(c: Connector, p: Record<string, unknown>) {
    await api(`/api/mcp/${encodeURIComponent(c.name)}`, { method: "PUT", body: { config: { ...c.config, ...p } } });
    load();
    onChanged();
  }
  async function test(c: Connector) {
    setTests({ ...tests, [c.name]: { ok: true, text: "正在连…" } });
    const r = await api<{ ok: boolean; error?: string; tools?: { name: string }[] }>("/api/mcp/test", { body: { name: c.name } });
    setTests({ ...tests, [c.name]: r.ok ? { ok: true, text: `已连上，${r.tools!.length} 个工具` } : { ok: false, text: r.error || "连不上" } });
    load();
  }

  return (
    <section className="page">
      <div className="ph" data-tauri-drag-region>
        <h1>连接器</h1>
        <span className="sp" data-tauri-drag-region />
        <button className="btn p" onClick={() => setEdit("new")}>添加连接器</button>
      </div>
      <div className="pb">
        <div className="sec-t">已接上</div>
        {list.length === 0 ? (
          <div className="list"><div className="li"><span className="ico"><Icon name="plug" /></span>
            <div className="tx"><b>还没接连接器</b><div className="d">接上 MCP 服务，队员就能用它的工具，比如地图、数据库、飞书文档</div></div>
            <button className="btn sm p" onClick={() => setEdit("new")}>添加</button></div></div>
        ) : (
          <div className="list">
            {list.map((c) => (
              <div className="li" key={c.name}>
                <ConnectorLogo id={String(c.config.preset || "")} size={22} />
                <div className="tx" onClick={() => setView(c)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setView(c)}>
                  <b>{c.name}</b>
                  <div className="d">
                    {c.transport === "http" ? String(c.config.url || "") : [c.config.command, ...((c.config.args as string[]) || [])].join(" ")}
                  </div>
                  {tests[c.name] && <div className={`note ${tests[c.name].ok ? "ok" : "bad"}`}>{tests[c.name].text}</div>}
                </div>
                <label className="note" style={{ display: "flex", alignItems: "center", gap: 6 }} title="调它的工具前先问你">
                  先问我
                  <button className={`tgl ${c.requires_approval ? "on" : ""}`} onClick={() => patch(c, { requires_approval: !c.requires_approval })} aria-label="先问我" />
                </label>
                <button className={`tgl ${c.enabled ? "on" : ""}`} onClick={() => patch(c, { enabled: !c.enabled })} aria-label="启用" title={c.enabled ? "停用" : "启用"} />
                <button className="btn sm" onClick={() => test(c)}>测试</button>
                <button className="btn sm" onClick={() => setEdit(c)}>编辑</button>
                <button className="icon-btn" aria-label="删除" onClick={async () => { await api(`/api/mcp/${encodeURIComponent(c.name)}`, { method: "DELETE" }); load(); onChanged(); }}><Icon name="trash" size={16} /></button>
              </div>
            ))}
          </div>
        )}
        <div className="sec-t">常用连接器<span style={{ fontWeight: 400, marginLeft: 8 }}>点一下，填一两个空就能接上</span></div>
        <div className="pgrid">
          {presets.map((p) => (
            <button key={p.id} className="preset" onClick={() => setPreset(p)}>
              <ConnectorLogo id={p.id} />
              <div>
                <b>{p.name}{p.added && <span className="tg">已接</span>}{!p.ready && <span className="tg x">要先装 {p.needs === "uvx" ? "uv" : "Node"}</span>}</b>
                <small>{p.desc}</small>
              </div>
            </button>
          ))}
        </div>
      </div>
      {preset && <PresetSheet p={preset} onClose={() => setPreset(null)} onSaved={(close) => { if (close !== false) setPreset(null); load(); onChanged(); }} />}
      {view && <ViewConnector c={view} onClose={() => setView(null)} onEdit={() => { setEdit(view); setView(null); }} />}
      {edit && <EditConnector c={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); onChanged(); }} />}
    </section>
  );
}

function EditConnector({ c, onClose, onSaved }: { c: Connector | null; onClose: () => void; onSaved: () => void }) {
  const [mode, setMode] = useState<"form" | "json">("form");
  const [name, setName] = useState(c?.name || "");
  const [kind, setKind] = useState<"stdio" | "http">(c?.transport || "stdio");
  const [cmd, setCmd] = useState(c ? [c.config.command, ...((c.config.args as string[]) || [])].filter(Boolean).join(" ") : "");
  const [url, setUrl] = useState(String(c?.config.url || ""));
  const [env, setEnv] = useState(c?.config.env ? JSON.stringify(c.config.env) : c?.config.headers ? JSON.stringify(c.config.headers) : "");
  const [json, setJson] = useState("");
  const [note, setNote] = useState({ cls: "", text: "" });

  function config(): Record<string, unknown> {
    const extra = env.trim() ? JSON.parse(env) : undefined;
    const keep = { enabled: c?.enabled ?? true, requires_approval: c?.requires_approval ?? true };
    if (kind === "http") return { url: url.trim(), ...(extra ? { headers: extra } : {}), ...keep };
    const parts = cmd.trim().match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
    const clean = parts.map((x) => x.replace(/^["']|["']$/g, ""));
    return { command: clean[0], args: clean.slice(1), ...(extra ? { env: extra } : {}), ...keep };
  }
  async function test() {
    try {
      setNote({ cls: "", text: "正在连…" });
      const r = await api<{ ok: boolean; error?: string; tools?: { name: string }[] }>("/api/mcp/test", { body: { name: name || "test", config: config() } });
      setNote(r.ok ? { cls: "ok", text: `已连上：${r.tools!.map((t) => t.name).slice(0, 6).join("、")}${r.tools!.length > 6 ? " 等" : ""}` } : { cls: "bad", text: r.error || "连不上" });
    } catch (e) {
      setNote({ cls: "bad", text: (e as Error).message.includes("JSON") ? "环境变量 / 请求头要是 JSON" : (e as Error).message });
    }
  }
  async function save() {
    try {
      if (mode === "json") await api("/api/mcp/import", { body: { json } });
      else {
        if (!name.trim()) return setNote({ cls: "bad", text: "要有名称" });
        if (c && c.name !== name.trim()) await api(`/api/mcp/${encodeURIComponent(c.name)}`, { method: "DELETE" });
        await api(`/api/mcp/${encodeURIComponent(name.trim())}`, { method: "PUT", body: { config: config() } });
      }
      onSaved();
    } catch (e) {
      setNote({ cls: "bad", text: `保存失败：${(e as Error).message}` });
    }
  }
  return (
    <Sheet title={c ? `编辑 ${c.name}` : "添加连接器"} onClose={onClose} foot={<>
      {mode === "form" && <button className="btn" onClick={test}>测试连接</button>}
      <span className={`note ${note.cls}`}>{note.text}</span><span className="sp" />
      <button className="btn p" onClick={save}>保存</button>
    </>}>
      {!c && (
        <div className="seg big" style={{ marginBottom: 14, width: "fit-content" }}>
          <button className={mode === "form" ? "on" : ""} onClick={() => setMode("form")}>填表</button>
          <button className={mode === "json" ? "on" : ""} onClick={() => setMode("json")}>粘贴配置</button>
        </div>
      )}
      {mode === "json" ? (
        <label className="field">
          <span>mcpServers 配置</span>
          <textarea className="input mono" style={{ minHeight: 220 }} value={json} onChange={(e) => setJson(e.target.value)}
            placeholder={'{\n  "mcpServers": {\n    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"]}\n  }\n}'} />
          <small>从 Claude Desktop、Cursor 的配置里整段复制过来就行</small>
        </label>
      ) : (
        <>
          <div className="grid2">
            <label className="field"><span>名称</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="比如：高德地图" /></label>
            <div className="field">
              <span>连接方式</span>
              <div className="seg big" style={{ width: "fit-content" }}>
                <button className={kind === "stdio" ? "on" : ""} onClick={() => setKind("stdio")}>本机命令</button>
                <button className={kind === "http" ? "on" : ""} onClick={() => setKind("http")}>网络地址</button>
              </div>
            </div>
          </div>
          {kind === "stdio" ? (
            <label className="field"><span>启动命令</span><input className="input mono" value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder="npx -y @modelcontextprotocol/server-filesystem ~/Documents" /></label>
          ) : (
            <label className="field"><span>服务地址</span><input className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/mcp" /></label>
          )}
          <label className="field">
            <span>{kind === "stdio" ? "环境变量" : "请求头"}</span>
            <input className="input mono" value={env} onChange={(e) => setEnv(e.target.value)} placeholder={kind === "stdio" ? '{"API_KEY": "…"}' : '{"Authorization": "Bearer …"}'} />
            <small>JSON；没有就空着</small>
          </label>
        </>
      )}
    </Sheet>
  );
}

function PresetSheet({ p, onClose, onSaved }: { p: McpPreset; onClose: () => void; onSaved: (close?: boolean) => void }) {
  const [vals, setVals] = useState<Record<string, string>>(Object.fromEntries(p.fields.map((f) => [f.key, f.default || ""])));
  const [name, setName] = useState(p.name);
  const [note, setNote] = useState({ cls: "", text: "" });
  const [busy, setBusy] = useState(false);
  async function add() {
    setBusy(true);
    setNote({ cls: "", text: p.needs ? "正在接…第一次要下载，可能要一两分钟" : "正在接…" });
    try {
      const r = await api<{ ok: boolean; test: { ok: boolean; error?: string; tools?: { name: string }[] } | null }>(
        `/api/mcp/presets/${p.id}`, { body: { values: vals, name, test: true } });
      if (r.test && !r.test.ok) {
        setNote({ cls: "bad", text: `已保存，但没连上：${r.test.error}` });
        setBusy(false);
        onSaved(false);
        return;
      }
      onSaved(true);
    } catch (e) {
      setNote({ cls: "bad", text: (e as Error).message });
      setBusy(false);
    }
  }
  return (
    <Sheet title={`接上${p.name}`} onClose={onClose} foot={<>
      <span className={`note ${note.cls}`}>{note.text}</span><span className="sp" />
      <button className="btn p" disabled={busy} onClick={add}>接上并测试</button>
    </>}>
      <p className="note" style={{ marginTop: 0 }}>{p.desc}{!p.ready && `；这台电脑还没装 ${p.needs === "uvx" ? "uv" : "Node.js"}，装好再接`}</p>
      <label className="field"><span>名称</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
      {p.fields.map((f) => (
        <label className="field" key={f.key}>
          <span>{f.label}</span>
          <input className="input" type={f.secret ? "password" : "text"} value={vals[f.key] || ""} autoComplete="off"
            onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
          {f.url && <small>去 <a href={f.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>这里</a> 拿</small>}
        </label>
      ))}
    </Sheet>
  );
}

function ViewConnector({ c, onClose, onEdit }: { c: Connector; onClose: () => void; onEdit: () => void }) {
  const [r, setR] = useState<{ ok: boolean; error?: string; tools?: { name: string; description: string }[] } | null>(null);
  useEffect(() => { api(`/api/mcp/${encodeURIComponent(c.name)}/tools`).then(setR as (x: unknown) => void, (e) => setR({ ok: false, error: (e as Error).message })); }, [c.name]);
  return (
    <Sheet title={c.name} wide onClose={onClose} foot={<><span className="sp" /><button className="btn" onClick={onEdit}>编辑</button></>}>
      <dl className="dl">
        <dt>连接方式</dt><dd>{c.transport === "http" ? "网络地址" : "本机命令"}</dd>
        <dt>{c.transport === "http" ? "地址" : "命令"}</dt>
        <dd className="mono sel">{c.transport === "http" ? String(c.config.url || "") : [c.config.command, ...((c.config.args as string[]) || [])].join(" ")}</dd>
        <dt>先问我</dt><dd>{c.requires_approval ? "调用前先问" : "直接调用"}</dd>
        <dt>状态</dt><dd>{!c.enabled ? "停用" : r === null ? "正在连…" : r.ok ? `已连上，${r.tools!.length} 个工具` : <span className="note bad">{r.error}</span>}</dd>
      </dl>
      {r?.ok && (
        <div className="list">
          {r.tools!.map((t) => (
            <div className="li" key={t.name}>
              <span className="ico"><Icon name="spark" /></span>
              <div className="tx"><b className="mono" style={{ fontSize: 12.5 }}>{t.name}</b><div className="d" title={t.description}>{t.description}</div></div>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  );
}
