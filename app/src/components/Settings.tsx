// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 设置浮层：通用（Yip 自己的规矩）、模型、关于。标题固定，正文滚动。
import { useEffect, useState } from "react";
import { api, rawUrl } from "../api";
import type { MailConfig, Preset, Settings as S, Soul, YipSettings } from "../types";
import { MODES } from "./Composer";
import { Icon } from "./Icon";
import { MailLogo, ProviderLogo } from "./Logo";
import { Sheet } from "./Sheet";
import icon from "../assets/icon.webp";

type Tab = "general" | "soul" | "model" | "mail" | "about";

export function SettingsSheet({ settings, initial = "general", onClose, onSaved }: {
  settings: S; initial?: Tab; onClose: () => void; onSaved: () => void;
}) {
  const [tab, setTab] = useState<Tab>(initial);
  const nav: [Tab, string, string][] = [["general", "gear", "通用"], ["soul", "spark", "性格"], ["model", "cpu", "模型"], ["mail", "mail", "邮箱"], ["about", "info", "关于"]];
  return (
    <Sheet title="设置" wide tall flush onClose={onClose}>
      <div className="settings">
        <nav>
          {nav.map(([t, ic, label]) => (
            <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              <Icon name={ic} />{label}
            </button>
          ))}
        </nav>
        <div className="pane">
          {tab === "general" && <General y={settings.yip} onSaved={onSaved} />}
          {tab === "soul" && <SoulPane y={settings.yip} onSaved={onSaved} />}
          {tab === "model" && <Model s={settings} onSaved={onSaved} />}
          {tab === "mail" && <Mail cfg={settings.mail || {}} onSaved={onSaved} />}
          {tab === "about" && <About />}
        </div>
      </div>
    </Sheet>
  );
}

const TONES: [Soul["tone"], string, string][] = [
  ["steady", "干练", "话少、准、利落，该关心时说一句"], ["warm", "温和", "语气软一点，多一句体贴"],
  ["cheeky", "嘴欠心软", "熟了偶尔怼一句，下一句就软回来"]];
const RELS: [Soul["relation"], string][] = [["assistant", "助理"], ["partner", "搭档"], ["friend", "朋友"]];
const EMOJI: [Soul["emoji"], string][] = [["few", "偶尔"], ["none", "不用"]];

function SoulPane({ y, onSaved }: { y: YipSettings; onSaved: () => void }) {
  const init: Soul = { name: "", call_me: "", tone: "steady", relation: "assistant", emoji: "few", intro: "", proactive: false, ...y.soul };
  const [f, setF] = useState<Soul>(init);
  const [note, setNote] = useState({ cls: "", text: "" });
  const [avVer, setAvVer] = useState(0);
  const [hasCustomAv, setHasCustomAv] = useState(false);
  useEffect(() => {
    fetch(rawUrl("/api/soul/avatar")).then((r) => setHasCustomAv(r.ok)).catch(() => {});
  }, [avVer]);
  const set = <K extends keyof Soul>(k: K, v: Soul[K]) => setF({ ...f, [k]: v });
  async function save() {
    try {
      await api("/api/settings", { method: "PUT", body: { yip: { soul: f } } });
      setNote({ cls: "ok", text: "已保存，下一句开始生效" });
      onSaved();
    } catch (e) {
      setNote({ cls: "bad", text: `保存失败：${(e as Error).message}` });
    }
  }
  async function pickAvatar(file: File) {
    try {
      const fd = new FormData();
      fd.append("file", file, file.name || "avatar.png");
      await api("/api/soul/avatar", { method: "POST", body: fd });
      setAvVer((v) => v + 1);
      setNote({ cls: "ok", text: "头像已换好" });
      onSaved();
    } catch (e) {
      setNote({ cls: "bad", text: `没换上：${(e as Error).message}` });
    }
  }
  async function resetAvatar() {
    await api("/api/soul/avatar", { method: "DELETE" });
    setHasCustomAv(false);
    setAvVer((v) => v + 1);
    setNote({ cls: "ok", text: "已用回茉莉" });
    onSaved();
  }
  return (
    <>
      <div className="acts" style={{ marginBottom: 14, alignItems: "center" }}>
        <img src={hasCustomAv ? rawUrl(`/api/soul/avatar?v=${avVer}`) : icon} alt=""
          style={{ width: 48, height: 48, borderRadius: "50%", objectFit: "cover", border: "1px solid var(--line)" }} />
        <label className="btn sm">
          换头像
          <input type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) pickAvatar(file);
          }} />
        </label>
        {hasCustomAv && <button className="btn sm" onClick={resetAvatar}>用回茉莉</button>}
        <span className="note">默认是茉莉，也可以换成你自己的图片</span>
      </div>
      <div className="grid2">
        <label className="field"><span>名字</span>
          <input className="input" maxLength={8} value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Yip" /></label>
        <label className="field"><span>怎么称呼你</span>
          <input className="input" maxLength={8} value={f.call_me} onChange={(e) => set("call_me", e.target.value)} placeholder="不填就不刻意称呼" /></label>
      </div>
      <div className="field">
        <span>性格</span>
        <div className="seg big" style={{ justifySelf: "start" }}>
          {TONES.map(([v, label]) => <button key={v} className={f.tone === v ? "on" : ""} onClick={() => set("tone", v)}>{label}</button>)}
        </div>
        <small>{TONES.find((t) => t[0] === f.tone)?.[2]}</small>
      </div>
      <div className="grid2">
        <div className="field">
          <span>你们的关系</span>
          <div className="seg big" style={{ justifySelf: "start" }}>
            {RELS.map(([v, label]) => <button key={v} className={f.relation === v ? "on" : ""} onClick={() => set("relation", v)}>{label}</button>)}
          </div>
        </div>
        <div className="field">
          <span>表情</span>
          <div className="seg big" style={{ justifySelf: "start" }}>
            {EMOJI.map(([v, label]) => <button key={v} className={f.emoji === v ? "on" : ""} onClick={() => set("emoji", v)}>{label}</button>)}
          </div>
        </div>
      </div>
      <label className="check"><input type="checkbox" checked={f.proactive} onChange={(e) => set("proactive", e.target.checked)} />工作日白天偶尔主动找我</label>
      <small className="note" style={{ margin: "-4px 0 12px 24px" }}>一天最多两次；晚上、周末不找；没回就不追</small>
      <label className="field">
        <span>别人问起时的自我介绍</span>
        <textarea className="input" value={f.intro} onChange={(e) => set("intro", e.target.value)}
          placeholder="不填就简单说名字和能干的活" />
      </label>
      <div className="acts pfoot">
        <button className="btn p" onClick={save}>保存</button>
        <span className={`note ${note.cls}`}>{note.text}</span>
      </div>
    </>
  );
}

function General({ y, onSaved }: { y: YipSettings; onSaved: () => void }) {
  const [f, setF] = useState(y);
  const [note, setNote] = useState({ cls: "", text: "" });
  const set = <K extends keyof YipSettings>(k: K, v: YipSettings[K]) => setF({ ...f, [k]: v });
  async function save() {
    try {
      await api("/api/settings", { method: "PUT", body: { yip: f } });
      setNote({ cls: "ok", text: "已保存" });
      onSaved();
    } catch (e) {
      setNote({ cls: "bad", text: `保存失败：${(e as Error).message}` });
    }
  }
  const mode = MODES.find(([m]) => m === f.approval) || MODES[0];
  return (
    <>
      <div className="sgroup">干活</div>
      <div className="field">
        <span>模式</span>
        <div className="seg big" style={{ justifySelf: "start" }}>
          {MODES.map(([v, label]) => <button key={v} className={mode[0] === v ? "on" : ""} onClick={() => set("approval", v)}>{label}</button>)}
        </div>
        <small>{mode[2]}；发邮件任何模式都问</small>
      </div>
      <div className="field">
        <span>干活的方式</span>
        <div className="seg big" style={{ justifySelf: "start" }}>
          {([["classic", "标准"], ["deep", "Deep Agents（试验）"]] as const).map(([v, label]) => (
            <button key={v} className={(f.engine || "classic") === v ? "on" : ""} onClick={() => set("engine", v)}>{label}</button>
          ))}
        </div>
        <small>{(f.engine || "classic") === "deep" ? "会先列计划、长任务自动压缩上下文；从下一件事生效" : "从下一件事生效"}</small>
      </div>
      <div className="grid2">
        <label className="field">
          <span>同时干活的队员</span>
          <input className="input" type="number" min={1} max={50} value={f.max_parallel} onChange={(e) => set("max_parallel", Number(e.target.value))} />
        </label>
        <label className="field">
          <span>每块最多几轮</span>
          <input className="input" type="number" min={1} max={50} value={f.max_rounds} onChange={(e) => set("max_rounds", Number(e.target.value))} />
          <small>轮数越多查得越细，也越慢</small>
        </label>
      </div>

      <div className="sgroup">文件和搜索</div>
      <label className="field">
        <span>工作文件夹</span>
        <input className="input" value={f.workspace} onChange={(e) => set("workspace", e.target.value)} />
        <small>队员只在这里读写，产物也放这里</small>
      </label>
      <div className="grid2">
        <label className="field">
          <span>搜索服务</span>
          <select className="input" value={f.search.provider} onChange={(e) => set("search", { ...f.search, provider: e.target.value as YipSettings["search"]["provider"] })}>
            <option value="keenable">Keenable（免密钥）</option>
            <option value="ddgs">DuckDuckGo（免密钥）</option>
            <option value="bocha">博查</option>
            <option value="tavily">Tavily</option>
          </select>
          <small>要挖得深时用 Tavily 连接器</small>
        </label>
        {f.search.provider !== "ddgs" && f.search.provider !== "keenable" && (
          <label className="field">
            <span>搜索服务密钥</span>
            <input className="input" type="password" value={f.search.api_key} onChange={(e) => set("search", { ...f.search, api_key: e.target.value })} />
          </label>
        )}
      </div>

      <div className="sgroup">给 Yip 的说明</div>
      <label className="field">
        <textarea className="input" value={f.note} onChange={(e) => set("note", e.target.value)}
          placeholder="比如：先结论后论据；数据标来源；给领导看的材料不写营销词" />
        <small>拆活、汇总、聊天时都会照这个来</small>
      </label>

      <details className="smore">
        <summary>更多</summary>
        <div className="grid3">
          <label className="field">
            <span>推翻要确认的进度</span>
            <input className="input" type="number" min={0} max={100} step={5} value={Math.round(f.overturn_confirm * 100)}
              onChange={(e) => set("overturn_confirm", Number(e.target.value) / 100)} />
            <small>超过这个百分比先问你</small>
          </label>
          <label className="field">
            <span>判断插话最多等</span>
            <input className="input" type="number" min={0.5} max={30} step={0.5} value={f.classify_timeout} onChange={(e) => set("classify_timeout", Number(e.target.value))} />
            <small>秒；超时改用本地规则</small>
          </label>
          <label className="field">
            <span>没把握的门槛</span>
            <input className="input" type="number" min={0} max={1} step={0.05} value={f.low_confidence} onChange={(e) => set("low_confidence", Number(e.target.value))} />
            <small>低于它先排着</small>
          </label>
        </div>
      </details>

      <div className="acts pfoot">
        <button className="btn p" onClick={save}>保存</button>
        <span className={`note ${note.cls}`}>{note.text}</span>
      </div>
    </>
  );
}

function Model({ s, onSaved }: { s: S; onSaved: () => void }) {
  const [pid, setPid] = useState(s.provider || s.presets[0].id);
  const preset = s.presets.find((p) => p.id === pid) as Preset;
  const saved = s.providers[pid];
  const [base, setBase] = useState("");
  const [key, setKey] = useState("");
  const [main, setMain] = useState("");
  const [fast, setFast] = useState("");
  const [vision, setVision] = useState("");
  const [note, setNote] = useState<{ cls: string; text: string; detail?: string }>({ cls: "", text: "" });
  const ref = (r: string) => (r.startsWith(`${pid}:`) ? r.slice(pid.length + 1) : "");
  useEffect(() => {
    setBase(saved?.base_url || preset.base_url);
    setKey(saved?.api_key || "");
    setMain(ref(s.model) || preset.model);
    setFast(ref(s.fast_model) || (ref(s.model) ? "" : preset.fast_model));
    setVision(ref(s.vision_model || ""));
    setNote({ cls: "", text: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);
  const using = s.model.startsWith(`${pid}:`);

  async function test() {
    setNote({ cls: "", text: "正在测试…" });
    const r = await api<{ ok: boolean; error?: string; detail?: string }>("/api/settings/test",
      { body: { provider: pid, base_url: base, api_key: key, model: main } });
    setNote(r.ok ? { cls: "ok", text: "已连上" } : { cls: "bad", text: r.error || "连不上", detail: r.detail });
  }
  async function save() {
    try {
      await api("/api/settings", { method: "PUT", body: {
        providers: { [pid]: { base_url: base, api_key: key } },
        model: main ? `${pid}:${main}` : s.model,
        fast_model: fast ? `${pid}:${fast}` : "",
        vision_model: vision ? `${pid}:${vision}` : (s.vision_model || "").startsWith(`${pid}:`) ? "" : s.vision_model || "",
      } });
      setNote({ cls: "ok", text: "已保存，接下来的活用它" });
      onSaved();
    } catch (e) {
      setNote({ cls: "bad", text: `保存失败：${(e as Error).message}` });
    }
  }

  return (
    <>
      <div className="pwall">
        {s.presets.map((p) => (
          <button key={p.id} className={`pcard ${pid === p.id ? "on" : ""}`} onClick={() => setPid(p.id)}>
            {s.model.startsWith(`${p.id}:`) && <span className="dot" title="正在用" />}
            <ProviderLogo id={p.id} size={22} />
            <span>
              <b>{p.name}</b>
              <small style={{ display: "block" }}>{s.providers[p.id]?.has_key || (s.providers[p.id] && !p.needs_key) ? "已配置" : p.needs_key ? "填密钥" : "本机"}</small>
            </span>
          </button>
        ))}
      </div>
      {preset.note && <div className="note" style={{ marginBottom: 10 }}>{preset.note}</div>}
      <label className="field">
        <span>服务地址</span>
        <input className="input" value={base} onChange={(e) => setBase(e.target.value)} placeholder="https://…/v1" />
      </label>
      {preset.needs_key && (
        <label className="field">
          <span>密钥</span>
          <input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
          {preset.key_url && <small>去 <a href={preset.key_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>{preset.name} 控制台</a> 拿</small>}
        </label>
      )}
      <div className="grid2">
        <label className="field">
          <span>日常模型</span>
          <input className="input" value={fast} onChange={(e) => setFast(e.target.value)} placeholder="不填就都用难活模型" />
          <small>聊天、拆活、队员干活都先用它，越快越好</small>
        </label>
        <label className="field">
          <span>难活模型</span>
          <input className="input" value={main} onChange={(e) => setMain(e.target.value)} />
          <small>难的活、返工、卡住时换它；验收也用它</small>
        </label>
      </div>
      <label className="field">
        <span>看图模型（可选）</span>
        <input className="input" value={vision} onChange={(e) => setVision(e.target.value)} placeholder="上面两个都看不了图时用，比如 qwen-vl-max、glm-4v" />
        <small>上面有一个能看图就不用填</small>
      </label>
      <div className="acts pfoot">
        <button className="btn" onClick={test}>测试连接</button>
        <button className="btn p" onClick={save}>{using ? "保存" : "保存并改用它"}</button>
        <span className={`note ${note.cls}`} title={note.detail || ""}>{note.text}</span>
      </div>
    </>
  );
}

interface MailPreset { id: string; name: string; imap: [string, number]; smtp: [string, number]; help: string }

function Mail({ cfg, onSaved }: { cfg: MailConfig; onSaved: () => void }) {
  const [presets, setPresets] = useState<MailPreset[]>([]);
  const [f, setF] = useState<MailConfig>({ provider: "qq", imap_port: 993, smtp_port: 465, ...cfg });
  const [more, setMore] = useState(f.provider === "custom");
  const [note, setNote] = useState<{ cls: string; text: string }>({ cls: "", text: "" });
  useEffect(() => { api<MailPreset[]>("/api/mail/presets").then(setPresets); }, []);
  const preset = presets.find((p) => p.id === f.provider);
  function pick(id: string) {
    const p = presets.find((x) => x.id === id);
    setF({ ...f, provider: id, imap_host: p?.imap[0] || "", imap_port: p?.imap[1] || 993, smtp_host: p?.smtp[0] || "", smtp_port: p?.smtp[1] || 465 });
    setMore(id === "custom");
  }
  const set = (k: keyof MailConfig) => (e: { target: { value: string } }) => setF({ ...f, [k]: k.endsWith("port") ? Number(e.target.value) : e.target.value });
  async function test() {
    setNote({ cls: "", text: "正在连…" });
    const r = await api<{ ok: boolean; text?: string; error?: string }>("/api/mail/test", { body: f });
    setNote(r.ok ? { cls: "ok", text: r.text || "通了" } : { cls: "bad", text: r.error || "连不上" });
  }
  async function save() {
    await api("/api/settings", { method: "PUT", body: { mail: f } });
    setNote({ cls: "ok", text: "已保存，队员现在能搜信、读信、起草了" });
    onSaved();
  }
  return (
    <>
      <div className="pwall">
        {presets.map((p) => (
          <button key={p.id} className={`pcard ${f.provider === p.id ? "on" : ""}`} onClick={() => pick(p.id)}>
            <MailLogo id={p.id} />
            <span><b>{p.name}</b></span>
          </button>
        ))}
      </div>
      {preset?.help && <div className="note" style={{ marginBottom: 10 }}>{preset.help}</div>}
      <div className="grid2">
        <label className="field"><span>邮箱地址</span><input className="input" value={f.address || ""} onChange={set("address")} placeholder="name@qq.com" /></label>
        <label className="field"><span>授权码</span><input className="input" type="password" autoComplete="off" value={f.password || ""} onChange={set("password")} />
          <small>不是登录密码，是邮箱设置里生成的那串</small></label>
      </div>
      <label className="field"><span>发信署名</span><input className="input" value={f.name || ""} onChange={set("name")} placeholder="收件人看到的名字，可不填" /></label>
      {more ? (
        <div className="grid2">
          <label className="field"><span>收信服务器（IMAP）</span><input className="input" value={f.imap_host || ""} onChange={set("imap_host")} /></label>
          <label className="field"><span>端口</span><input className="input" type="number" value={f.imap_port || 993} onChange={set("imap_port")} /></label>
          <label className="field"><span>发信服务器（SMTP）</span><input className="input" value={f.smtp_host || ""} onChange={set("smtp_host")} /></label>
          <label className="field"><span>端口</span><input className="input" type="number" value={f.smtp_port || 465} onChange={set("smtp_port")} /></label>
        </div>
      ) : (
        <button className="lk" style={{ marginBottom: 12 }} onClick={() => setMore(true)}>服务器地址 ›</button>
      )}
      <div className="note" style={{ marginBottom: 12 }}>队员发信前每封都会先问你，「全部放行」也一样</div>
      <div className="acts pfoot">
        <button className="btn" onClick={test}>测试连接</button>
        <button className="btn p" onClick={save}>保存</button>
        <span className={`note ${note.cls}`}>{note.text}</span>
      </div>
    </>
  );
}

function About() {
  return (
    <div className="about">
      <img src={icon} alt="" />
      <h3>Yip</h3>
      <p>{__APP_VERSION__}</p>
      <p>yip 是草原犬鼠站岗时提醒同伴的那一声</p>
      <p>iML Studio 出品 · Apache-2.0 许可</p>
    </div>
  );
}
