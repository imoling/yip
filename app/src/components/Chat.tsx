// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 和 Yip 的唯一一条对话：按时间分段，派活卡片、回执、文件卡片穿插其中。
import { forwardRef, useEffect, useRef, useState, type RefObject } from "react";
import { api, rawUrl } from "../api";
import type { AppState, Artifact, Message, StarterPack, Worker } from "../types";
import { dayLabel, isLive } from "../util";
import { Composer, type ComposerHandle } from "./Composer";
import { Icon } from "./Icon";
import { MessageRow, Say } from "./Messages";
import { Tray } from "./Tray";
import icon from "../assets/icon.webp";
import wave from "../assets/wave.webp";
import working from "../assets/working.webp";
import animIdle from "../assets/anim/idle.mp4";
import animThink from "../assets/anim/think.mp4";
import animWork from "../assets/anim/working.mp4";
import animAsk from "../assets/anim/ask.mp4";

const ANIM = { idle: animIdle, think: animThink, work: animWork, ask: animAsk };  // 茉莉的四段循环动画

interface Props {
  state: AppState;
  workers: Worker[];
  pending: string | null;
  thinking: boolean;
  sending: boolean;
  error: string;
  railOpen: boolean;
  onToggleRail: () => void;
  browserOpen: boolean;
  onToggleBrowser: () => void;
  onSend: (t: string, attachments: number[]) => Promise<boolean>;
  onOpenFile: (a: Artifact) => void;
  onConnectors: () => void;
  onMemory: () => void;
  onAutomations: () => void;
  onMember: (taskId: string) => void;
  onHistory: () => void;
  onClear: () => void;
  clearedAt: number;  // 刚点了清屏、前情还在写：先按这个 id 把界面清掉
  onFolder: () => void;
  onFolderChanged: () => void;
  onError: (m: string) => void;
  onChanged: () => void;
}

const STARTERS = [
  "帮我查一下 DeepSeek 和 Kimi 的 API 公开定价，整理成一页对比",
  "整理三家竞品的简报，重点看定价和渠道",
  "工作文件夹里有哪些文件？",
];

export const Chat = forwardRef<ComposerHandle, Props>(function Chat(p, composer) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [packs, setPacks] = useState<StarterPack[]>([]);
  const [goalFilter, setGoalFilter] = useState("");
  useEffect(() => {
    api<StarterPack[]>("/api/starters").then(setPacks).catch(() => {});
  }, []);
  // 清屏只清界面：只显示最后一次清屏之后的话，之前的在「聊天记录」里
  const all = p.state.messages as Message[];
  const cut = Math.max(p.clearedAt, all.reduce((n, m) => (m.meta?.kind === "clear" ? m.id : n), 0));
  const afterClear = cut ? all.filter((m) => m.id > cut) : all;
  const goalTaskIds = goalFilter ? new Set(p.state.jobs.filter((j) => j.goal_id === goalFilter).map((j) => j.id)) : null;
  const msgs = goalTaskIds ? afterClear.filter((m) => m.task_id && goalTaskIds.has(m.task_id)) : afterClear;
  const live = p.state.jobs.filter((j) => isLive(j));
  const busy = live.flatMap((j) => j.children).filter((k) => k.status === "running").length;
  // 拆完等主人看大纲 / 计划：不算「正在拆」，算等你确认
  const outline = live.filter((j) => j.children.length === 0 && j.note === "等你点头");
  const planning = live.some((j) => j.children.length === 0 && j.note !== "等你点头");
  const liveIds = new Set(live.map((j) => j.id));
  const asks = p.state.approvals.length
    + p.state.decisions.filter((d) => d.status === "pending_confirm" && liveIds.has(d.task_id)).length
    + outline.length;
  const thinkingNow = !!p.pending || p.thinking || planning;
  // 四种状态：等你确认 > 干活 > 想 > 待命
  const mood = asks ? "ask" : busy ? "work" : thinkingNow ? "think" : "idle";
  const still = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;  // 减少动态效果：用静态图
  const face = mood === "work" ? working : mood === "ask" ? wave : icon;
  const label = mood === "ask" ? (outline.length && asks === outline.length ? "等你看大纲" : `${asks} 件事等你确认`) : mood === "work" ? `${busy} 位队员在干活` : mood === "think" ? (planning ? "正在拆活" : "正在想") : "随时待命";

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [msgs.length, p.pending, p.thinking, p.state.approvals.length]);

  const ctx = { state: p.state, workers: p.workers, onOpenFile: p.onOpenFile, onError: p.onError, onChanged: p.onChanged, onMemory: p.onMemory, onAutomations: p.onAutomations, onMember: p.onMember, onSay: (t: string) => { p.onSend(t, []); } };
  const rows: JSX.Element[] = [];
  let last = 0;
  // 主人又说了话，之前消息下的按钮就收起来
  const lastYou = Math.max(0, ...(msgs as Message[]).filter((m) => m.role === "user").map((m) => m.id));
  for (const m of msgs as Message[]) {
    if (m.role === "decision") continue;
    if (m.created_at - last > 30 * 60 * 1000) rows.push(<div className="day" key={`d${m.id}`}>{dayLabel(m.created_at)}</div>);
    last = m.created_at;
    rows.push(<MessageRow key={m.id} m={m} ctx={ctx} stale={m.id < lastYou} />);
  }

  return (
    <main className="main">
      <div className="top" data-tauri-drag-region>
        <div className={`yip ${mood}`} role="status" aria-live="polite">
          <span className="face">
            {p.state.avatar ? (
              <img src={rawUrl(p.state.avatar)} alt="" />
            ) : still ? (
              <img src={face} alt="" />
            ) : (
              <video key={mood} src={ANIM[mood]} autoPlay loop muted playsInline aria-hidden="true" />
            )}
          </span>
          <b>{p.state.name || "Yip"}</b>
          <span>{label}</span>
        </div>
        <div className="r">
          {(p.state.goals || []).length > 0 && (
            <select className="input" style={{ height: 28, padding: "0 8px", fontSize: 12, width: "auto" }}
              value={goalFilter} onChange={(e) => setGoalFilter(e.target.value)} aria-label="按目标筛选">
              <option value="">全部对话</option>
              {(p.state.goals || []).map((g) => <option key={g.id} value={g.id}>跟进：{g.title}</option>)}
            </select>
          )}
          <button className="icon-btn" onClick={p.onHistory} title="聊天记录"><Icon name="history" /></button>
          <button className="icon-btn" onClick={p.onClear} disabled={!msgs.length} title="清屏：之前的话在聊天记录里，Yip 也记得"><Icon name="eraser" /></button>
          <button className={`icon-btn ${p.browserOpen ? "on" : ""}`} onClick={p.onToggleBrowser} title={p.browserOpen ? "收起浏览器" : "打开浏览器"}>
            <Icon name="globe" />
          </button>
          <button className="icon-btn" onClick={p.onToggleRail} title={p.railOpen ? "收起右栏" : "展开右栏"}>
            <Icon name="rail" />
          </button>
        </div>
      </div>
      <div className="chat" ref={box} onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}>
        {msgs.length === 0 && !p.pending ? (
          <div className="empty">
            <img src={wave} alt="" />
            <h2>有事交给我</h2>
            <ul>
              <li><Icon name="split" />一件事拆成几块，派给对口的队员同时做</li>
              <li><Icon name="swap" />中途直接插话，补充、追加、推翻我自己判断</li>
              <li><Icon name="doc" />做完的文件放进工作文件夹，对话里直接打开</li>
            </ul>
            <div className="starters">
              {(packs.length ? packs : STARTERS.map((s, i): StarterPack => ({ id: String(i), title: s, tag: "", prompt: s, mode: "once" }))).map((s) => (
                <button key={s.id} className="starter" onClick={async () => {
                  if (s.mode === "schedule" && s.rule) {
                    try {
                      await api("/api/automations", { body: { name: s.title, text: s.prompt, rule: s.rule, enabled: true } });
                      p.onAutomations();
                    } catch (e) {
                      p.onError(`没建成：${(e as Error).message}`);
                    }
                  } else {
                    (composer as RefObject<ComposerHandle>).current?.fill(s.prompt);
                  }
                }}>
                  <Icon name={s.mode === "schedule" ? "timer" : "arrow"} />
                  <span>{s.title}{s.when ? ` · ${s.when}` : ""}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="col">
            {rows}
            {p.pending && <div className="you pend"><span>{p.pending}</span></div>}
            {(p.pending || p.thinking) && <Say think>正在想<span className="dots"><i /><i /><i /></span></Say>}
            {!p.pending && !p.thinking && planning && <Say think>正在拆成几块，挑对口的队员<span className="dots"><i /><i /><i /></span></Say>}
            <div style={{ height: 24 }} />
          </div>
        )}
      </div>
      <Composer
        ref={composer}
        sending={p.sending}
        error={p.error}
        workspace={p.state.workspace}
        connectors={p.state.connectors}
        workers={p.workers}
        onSend={p.onSend}
        onFolderSettings={p.onFolder}
        onFolderChanged={p.onFolderChanged}
        onConnectors={p.onConnectors}
        tray={<Tray state={p.state} workers={p.workers} onError={p.onError} onChanged={p.onChanged} />}
      />
    </main>
  );
});
