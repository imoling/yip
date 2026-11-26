// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 跟进与定时：长期目标（有状态持续推进）+ 定时自动化（到点把一句话交给 Yip）。
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Goal, StarterPack } from "../types";
import { when } from "../util";
import { Icon } from "./Icon";
import { Sheet } from "./Sheet";

type Rule =
  | { type: "daily" | "weekdays"; time: string }
  | { type: "weekly"; time: string; days: number[] }
  | { type: "monthly"; time: string; day: number }
  | { type: "interval"; hours: number }
  | { type: "once"; at: string };

interface Auto {
  id: number;
  name: string;
  rule: Rule;
  text: string;
  enabled: number;
  when: string;
  next_at: number | null;
  last_at: number | null;
  last_status: string | null;
  last_title: string | null;
}

const STATUS: Record<string, string> = { done: "做完了", running: "在做", failed: "中断了", cancelled: "取消了", pending: "在做" };
const GOAL_STATUS: Record<Goal["status"], string> = { active: "进行中", paused: "已暂停", done: "已完成" };
const WEEK = "一二三四五六日";

export function Automations({ inShell, onOpenChat }: { inShell: boolean; onOpenChat: () => void }) {
  const [tab, setTab] = useState<"goals" | "autos">("goals");
  const [list, setList] = useState<Auto[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [packs, setPacks] = useState<StarterPack[]>([]);
  const [edit, setEdit] = useState<Partial<Auto> | null>(null);
  const [editGoal, setEditGoal] = useState<Partial<Goal> | null>(null);
  const [note, setNote] = useState("");
  const loadAutos = () => api<Auto[]>("/api/automations").then(setList);
  const loadGoals = () => api<Goal[]>("/api/goals").then(setGoals);
  const loadAll = () => { loadAutos(); loadGoals(); };
  useEffect(() => {
    loadAll();
    api<StarterPack[]>("/api/starters").then((r) => setPacks(r.filter((x) => x.mode === "schedule"))).catch(() => {});
    const t = setInterval(loadAll, 15000);
    return () => clearInterval(t);
  }, []);

  async function toggle(a: Auto) {
    await api(`/api/automations/${a.id}`, { method: "PUT", body: { enabled: !a.enabled } });
    loadAutos();
  }
  async function run(a: Auto) {
    await api(`/api/automations/${a.id}/run`, { method: "POST" });
    setNote(`已开跑：${a.name}，去对话里看`);
    loadAutos();
  }
  async function reviewGoal(g: Goal) {
    await api(`/api/goals/${g.id}/review`, { method: "POST" });
    setNote(`已开始复盘「${g.title}」，去对话里看`);
    loadGoals();
  }
  async function setGoalStatus(g: Goal, status: Goal["status"]) {
    await api(`/api/goals/${g.id}`, { method: "PUT", body: { status } });
    loadGoals();
  }
  async function applyPack(p: StarterPack) {
    if (!p.rule) return;
    await api("/api/automations", { body: { name: p.title, text: p.prompt, rule: p.rule, enabled: true } });
    setNote(`已添加：${p.title}`);
    setTab("autos");
    loadAutos();
  }

  return (
    <section className="page">
      <div className="ph" data-tauri-drag-region>
        <h1>跟进与定时</h1>
        <div className="seg big" style={{ marginLeft: 10 }}>
          <button className={tab === "goals" ? "on" : ""} onClick={() => setTab("goals")}>长期跟进{goals.length ? ` ${goals.length}` : ""}</button>
          <button className={tab === "autos" ? "on" : ""} onClick={() => setTab("autos")}>定时任务{list.length ? ` ${list.length}` : ""}</button>
        </div>
        <span className="note ok">{note}</span>
        <span className="sp" data-tauri-drag-region />
        {tab === "goals" ? (
          <button className="btn p" onClick={() => setEditGoal({})}>新建跟进</button>
        ) : (
          <button className="btn p" onClick={() => setEdit({})}>新建定时</button>
        )}
      </div>
      <div className="pb">
        <div className="note" style={{ margin: "0 4px 12px" }}>
          {inShell ? "关掉窗口后 Yip 会留在菜单栏继续按时跑；要我确认的事会发通知" : "要按时跑，Yip 得开着"}
        </div>

        {tab === "goals" && (
          goals.length === 0 ? (
            <div className="list"><div className="li"><span className="ico"><Icon name="spark" /></span>
              <div className="tx"><b>还没有长期跟进</b><div className="d">跨周或跨月的事放这里，每次复盘会带上历史进展和记忆，不用从头说起</div></div>
              <button className="btn sm p" onClick={() => setEditGoal({})}>新建一个</button></div></div>
          ) : (
            <div className="cards">
              {goals.map((g) => (
                <div className={`card ${g.status === "active" ? "" : "off"}`} key={g.id}>
                  <div className="h">
                    <span className="logo-tile" style={{ width: 44, height: 44, color: "var(--accent)", background: "color-mix(in srgb, var(--accent) 12%, var(--card))" }}><Icon name="spark" size={20} /></span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <b>{g.title}</b>
                      <div className="note">{GOAL_STATUS[g.status]} · 第 {g.days} 天{g.when ? ` · ${g.when}` : ""}</div>
                    </div>
                  </div>
                  <p className="sel">{g.target}</p>
                  {g.progress_note && <div className="note">进展：{g.progress_note}</div>}
                  <div className="note">
                    已推进 {g.job_count} 次 · 产物 {g.artifacts.length} 份
                    {g.last_review_at ? ` · 上次复盘 ${when(g.last_review_at)}` : ""}
                  </div>
                  <div className="acts">
                    <button className="btn sm p" onClick={() => reviewGoal(g)}>现在复盘</button>
                    <button className="btn sm" onClick={() => setEditGoal(g)}>编辑</button>
                    {g.status === "active" ? (
                      <button className="btn sm" onClick={() => setGoalStatus(g, "paused")}>暂停</button>
                    ) : (
                      <button className="btn sm" onClick={() => setGoalStatus(g, "active")}>继续</button>
                    )}
                    {g.status !== "done" && <button className="btn sm" onClick={() => setGoalStatus(g, "done")}>标为完成</button>}
                    {g.last_job && <button className="btn sm" onClick={onOpenChat}>看对话</button>}
                    <span className="sp" />
                    <button className="icon-btn" aria-label="删除" onClick={async () => { await api(`/api/goals/${g.id}`, { method: "DELETE" }); loadGoals(); }}><Icon name="trash" size={15} /></button>
                  </div>
                </div>
              ))}
            </div>
          )
        )}

        {tab === "autos" && (
          <>
            {list.length === 0 ? (
              <div className="list"><div className="li"><span className="ico"><Icon name="timer" /></span>
                <div className="tx"><b>还没有定时任务</b><div className="d">比如：每个工作日早上 8 点整理昨天的邮件；每周五下午 5 点写周报</div></div>
                <button className="btn sm p" onClick={() => setEdit({})}>新建一个</button></div></div>
            ) : (
              <div className="cards">
                {list.map((a) => (
                  <div className={`card ${a.enabled ? "" : "off"}`} key={a.id}>
                    <div className="h">
                      <span className="logo-tile" style={{ width: 44, height: 44, color: "var(--accent)", background: "color-mix(in srgb, var(--accent) 12%, var(--card))" }}><Icon name="timer" size={22} /></span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <b>{a.name}</b>
                        <div className="note">{a.when}</div>
                      </div>
                      <button className={`tgl ${a.enabled ? "on" : ""}`} onClick={() => toggle(a)} aria-label={a.enabled ? "停用" : "启用"} title={a.enabled ? "停用" : "启用"} />
                    </div>
                    <p className="sel">{a.text}</p>
                    <div className="note">
                      {a.enabled && a.next_at ? `下次：${new Date(a.next_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit" })}` : "已停用"}
                      {a.last_at ? ` · 上次 ${when(a.last_at)}${a.last_status ? ` ${STATUS[a.last_status] || a.last_status}` : ""}` : ""}
                    </div>
                    <div className="acts">
                      <button className="btn sm" onClick={() => run(a)}>现在跑一次</button>
                      <button className="btn sm" onClick={() => setEdit(a)}>编辑</button>
                      {a.last_at && <button className="btn sm" onClick={onOpenChat}>看结果</button>}
                      <span className="sp" />
                      <button className="icon-btn" aria-label="删除" onClick={async () => { await api(`/api/automations/${a.id}`, { method: "DELETE" }); loadAutos(); }}><Icon name="trash" size={15} /></button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {packs.length > 0 && (
              <div style={{ marginTop: 18 }}>
                <div className="sec-t">一键场景包</div>
                <div className="cards">
                  {packs.map((p) => (
                    <div className="card" key={p.id}>
                      <b>{p.title}</b>
                      <div className="note">{p.when}</div>
                      <p className="sel">{p.prompt}</p>
                      <div className="acts">
                        <button className="btn sm" onClick={() => applyPack(p)}>直接启用</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
      {edit && <Edit init={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); loadAutos(); }} />}
      {editGoal && <EditGoal init={editGoal} onClose={() => setEditGoal(null)} onSaved={() => { setEditGoal(null); loadGoals(); }} />}
    </section>
  );
}

function EditGoal({ init, onClose, onSaved }: { init: Partial<Goal>; onClose: () => void; onSaved: () => void }) {
  const [title, setTitle] = useState(init.title || "");
  const [target, setTarget] = useState(init.target || "");
  const [progressNote, setProgressNote] = useState(init.progress_note || "");
  const [autoReview, setAutoReview] = useState(Boolean(init.rule));
  const [time, setTime] = useState((init.rule as { time?: string } | null)?.time || "09:00");
  const [freq, setFreq] = useState<"daily" | "weekdays" | "weekly">(((init.rule as { type?: string } | null)?.type as "daily" | "weekdays" | "weekly") || "weekly");
  const [note, setNote] = useState("");

  async function save() {
    try {
      const rule = autoReview
        ? (freq === "weekly" ? { type: "weekly", time, days: [1] } : { type: freq, time })
        : null;
      const body = { title, target, progress_note: progressNote, rule, status: init.status || "active" };
      if (init.id) await api(`/api/goals/${init.id}`, { method: "PUT", body });
      else await api("/api/goals", { body });
      onSaved();
    } catch (e) {
      setNote((e as Error).message);
    }
  }

  return (
    <Sheet title={init.id ? "编辑长期跟进" : "新建长期跟进"} wide onClose={onClose} foot={<>
      <span className="note bad">{note}</span><span className="sp" />
      <button className="btn p" disabled={!target.trim()} onClick={save}>保存</button>
    </>}>
      <label className="field">
        <span>目标名称</span>
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus placeholder="比如：下月产品汇报 / 三家竞品追踪" />
      </label>
      <label className="field">
        <span>具体要跟进什么</span>
        <textarea className="input" rows={3} value={target} onChange={(e) => setTarget(e.target.value)}
          placeholder="写清楚要持续追踪或推进的事、关心哪些变化" />
      </label>
      <label className="field">
        <span>当前进展（可选）</span>
        <input className="input" value={progressNote} onChange={(e) => setProgressNote(e.target.value)} placeholder="目前做到哪一步了" />
      </label>
      <label className="check">
        <input type="checkbox" checked={autoReview} onChange={(e) => setAutoReview(e.target.checked)} />
        定期自动复盘并汇报变化
      </label>
      {autoReview && (
        <div className="acts" style={{ marginTop: 8 }}>
          <div className="seg big">
            <button className={freq === "daily" ? "on" : ""} onClick={() => setFreq("daily")}>每天</button>
            <button className={freq === "weekdays" ? "on" : ""} onClick={() => setFreq("weekdays")}>工作日</button>
            <button className={freq === "weekly" ? "on" : ""} onClick={() => setFreq("weekly")}>每周一</button>
          </div>
          <input className="input" type="time" style={{ width: 120 }} value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
      )}
    </Sheet>
  );
}

function Edit({ init, onClose, onSaved }: { init: Partial<Auto>; onClose: () => void; onSaved: () => void }) {
  const [say, setSay] = useState("");
  const [name, setName] = useState(init.name || "");
  const [text, setText] = useState(init.text || "");
  const [rule, setRule] = useState<Rule>(init.rule || { type: "weekdays", time: "08:00" });
  const [note, setNote] = useState({ cls: "", text: "" });
  const [busy, setBusy] = useState(false);

  async function parse() {
    setBusy(true);
    setNote({ cls: "", text: "正在听…" });
    try {
      const r = await api<{ name: string; rule: Rule; text: string; when: string }>("/api/automations/parse", { body: { text: say } });
      setName(r.name);
      setText(r.text);
      setRule(r.rule);
      setNote({ cls: "ok", text: `听成了：${r.when}，看看下面对不对` });
    } catch (e) {
      setNote({ cls: "bad", text: (e as Error).message });
    }
    setBusy(false);
  }
  async function save() {
    try {
      const body = { name, text, rule, enabled: true };
      if (init.id) await api(`/api/automations/${init.id}`, { method: "PUT", body });
      else await api("/api/automations", { body });
      onSaved();
    } catch (e) {
      setNote({ cls: "bad", text: (e as Error).message });
    }
  }
  const t = "time" in rule ? rule.time : "09:00";
  const kinds: [Rule["type"], string][] = [["daily", "每天"], ["weekdays", "工作日"], ["weekly", "每周"], ["monthly", "每月"], ["interval", "每隔"], ["once", "只一次"]];
  function setType(k: Rule["type"]) {
    if (k === "weekly") setRule({ type: k, time: t, days: [1] });
    else if (k === "monthly") setRule({ type: k, time: t, day: 1 });
    else if (k === "interval") setRule({ type: k, hours: 1 });
    else if (k === "once") {
      const d = new Date(Date.now() + 3600000);
      const p = (n: number) => String(n).padStart(2, "0");
      setRule({ type: k, at: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:00` });
    } else setRule({ type: k, time: t });
  }
  return (
    <Sheet title={init.id ? "编辑定时" : "新建定时"} wide onClose={onClose} foot={<>
      <span className={`note ${note.cls}`}>{note.text}</span><span className="sp" />
      <button className="btn p" disabled={!text.trim()} onClick={save}>保存</button>
    </>}>
      {!init.id && (
        <div className="field">
          <span>一句话说</span>
          <div style={{ display: "flex", gap: 8 }}>
            <input className="input" value={say} onChange={(e) => setSay(e.target.value)} autoFocus placeholder="每个工作日早上 8 点整理昨天的邮件，告诉我哪些要处理"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing && say.trim()) parse(); }} />
            <button className="btn" disabled={busy || !say.trim()} onClick={parse}>拆开</button>
          </div>
        </div>
      )}
      <div className="field">
        <span>什么时候</span>
        <div className="seg big" style={{ width: "fit-content" }}>
          {kinds.map(([k, label]) => <button key={k} className={rule.type === k ? "on" : ""} onClick={() => setType(k)}>{label}</button>)}
        </div>
      </div>
      <div className="acts" style={{ marginBottom: 14 }}>
        {rule.type === "weekly" && WEEK.split("").map((c, i) => (
          <button key={c} className={`chip ${rule.days.includes(i + 1) ? "on" : ""}`}
            onClick={() => setRule({ ...rule, days: rule.days.includes(i + 1) ? rule.days.filter((d) => d !== i + 1) : [...rule.days, i + 1].sort() })}>周{c}</button>
        ))}
        {rule.type === "monthly" && (
          <label className="note" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input className="input" type="number" min={1} max={31} style={{ width: 70 }} value={rule.day} onChange={(e) => setRule({ ...rule, day: Number(e.target.value) })} />号
          </label>
        )}
        {rule.type === "interval" && (
          <label className="note" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input className="input" type="number" min={0.1} step={0.5} style={{ width: 80 }} value={rule.hours} onChange={(e) => setRule({ ...rule, hours: Number(e.target.value) })} />小时
          </label>
        )}
        {rule.type === "once" ? (
          <input className="input" type="datetime-local" style={{ width: 220 }} value={rule.at.replace(" ", "T")} onChange={(e) => setRule({ type: "once", at: e.target.value.replace("T", " ") })} />
        ) : rule.type !== "interval" && (
          <input className="input" type="time" style={{ width: 120 }} value={t} onChange={(e) => setRule({ ...rule, time: e.target.value } as Rule)} />
        )}
      </div>
      <label className="field">
        <span>到点做什么</span>
        <textarea className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="和在对话里跟 Yip 说的一样，比如：整理昨天的邮件，告诉我哪些要我处理" />
      </label>
      <label className="field"><span>名字</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="不填就用上面那句话的开头" /></label>
    </Sheet>
  );
}
