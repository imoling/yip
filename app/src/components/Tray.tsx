// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 等你确认：照 Claude Code，一次问一件，问题、要做的事、编号选项摆在输入框上方；按 1 到 3 或上下键加回车选。
// 「自动」模式下只有发出去、删东西、碰工作文件夹以外的才会来问；超过门槛的推翻也在这里。
import { useEffect, useState } from "react";
import { api } from "../api";
import type { AppState, Approval, Decision, Worker } from "../types";
import { isLive, pct, taskOf } from "../util";
import { avatarOf } from "./Avatar";
import icon from "../assets/icon.webp";

interface Props {
  state: AppState;
  workers: Worker[];
  onError: (m: string) => void;
  onChanged: () => void;
}

type Opt = [string, () => Promise<void>];
const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : JSON.stringify(v));

function detail(a: Approval): string {
  const g = a.args || {};
  if (a.tool === "mail_send" || a.tool === "mail_draft")
    return [`收件人：${str(g.to)}`, g.cc ? `抄送：${str(g.cc)}` : "", `主题：${str(g.subject)}`, str(g.body).slice(0, 300)].filter(Boolean).join("\n");
  if (g.command) return str(g.command).slice(0, 600);
  if (g.content !== undefined) return `${a.summary}\n${str(g.content).slice(0, 240)}`;
  return a.summary;
}

function question(a: Approval, who: string): string {
  if (a.tool === "mail_send") return "发这封邮件吗？";
  if (a.tool.startsWith("computer_")) return `${who}要操作你的电脑：${a.summary}，可以吗？`;
  if (a.tool === "mail_draft") return "存成邮件草稿吗？";
  if (a.risk === "exec") return `${who}要跑这条命令，可以吗？`;
  if (a.risk === "external") return `${who}要调连接器，可以吗？`;
  if (a.risk === "write") return `${who}要写这个文件，可以吗？`;
  return `${who}要做这一步，可以吗？`;
}

export function Tray({ state, workers, onError, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState(0);
  const [picked, setPicked] = useState<string[]>([]);
  const [custom, setCustom] = useState("");
  const liveIds = new Set(state.jobs.filter((j) => isLive(j)).map((j) => j.id));
  const decs = state.decisions.filter((d) => d.status === "pending_confirm" && liveIds.has(d.task_id));
  const asks = state.approvals;
  const n = decs.length + asks.length;
  const dec: Decision | undefined = decs[0];
  const ask: Approval | undefined = dec ? undefined : asks[0];
  const first = dec || ask;
  const key = dec ? `d${dec.id}` : ask ? `a${ask.id}` : "";
  useEffect(() => { setSel(0); setPicked([]); setCustom(""); }, [key]);

  async function go(path: string, body?: unknown) {
    setBusy(true);
    try {
      await api(path, { method: "POST", body });
      onChanged();
    } catch (e) {
      onError(`没处理成：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  let head = "", body = "", face = icon, opts: Opt[] = [];
  const isAskUser = Boolean(ask && ask.tool === "ask_user");
  const askMulti = Boolean(isAskUser && ask?.args?.multi);
  const askChoices: string[] = isAskUser && Array.isArray(ask?.args?.options)
    ? (ask?.args?.options as unknown[]).map((x) => String(x || "").trim()).filter(Boolean).slice(0, 5)
    : [];
  if (dec) {
    const d = dec;
    const t = taskOf(state.jobs, d.target);
    const who = t && t.parent_id ? `「${t.title}」` : "整件事";
    head = `${who}要推翻重来吗？`;
    body = `已经做了 ${pct(d.progress)}，推翻就丢掉${d.detail ? `，改做「${d.detail}」` : ""}`;
    opts = [["推翻重来", () => go(`/api/decisions/${d.id}/confirm`)],
            ["先别推翻，排在后面做", () => go(`/api/decisions/${d.id}/override`, { intent: "append" })]];
  } else if (ask) {
    const a = ask;
    face = avatarOf(a.worker_id, workers);
    head = isAskUser ? str(a.args?.question || a.summary) : question(a, workers.find((w) => w.id === a.worker_id)?.name || "队员");
    body = isAskUser ? "" : detail(a);
    if (a.tool === "browser_login") {  // 请主人在弹出的窗口里登录
      head = a.summary;
      body = str(a.args?.url);
    }
    if (isAskUser && !askMulti) {
      opts = [
        ...askChoices.map((c): Opt => [c, () => go(`/api/approvals/${a.id}`, { decision: "allow", answer: c })]),
        ["你定吧", () => go(`/api/approvals/${a.id}`, { decision: "deny" })],
      ];
    } else if (!isAskUser) {
      opts = a.tool === "browser_login"
        ? [["登好了", () => go(`/api/approvals/${a.id}`, { decision: "allow" })],
           ["不登了", () => go(`/api/approvals/${a.id}`, { decision: "deny" })]]
        : a.tool === "mail_send"
        ? [["发", () => go(`/api/approvals/${a.id}`, { decision: "allow" })],
           ["先别发", () => go(`/api/approvals/${a.id}`, { decision: "deny" })]]
        : a.tool.startsWith("computer_")
        ? [["可以", () => go(`/api/approvals/${a.id}`, { decision: "allow" })],
           ["可以，这件事里的电脑操作都允许", () => go(`/api/approvals/${a.id}`, { decision: "allow_task" })],
           ["不行", () => go(`/api/approvals/${a.id}`, { decision: "deny" })]]
        : [["可以", () => go(`/api/approvals/${a.id}`, { decision: "allow" })],
           ["可以，这件事里同类的都别再问", () => go(`/api/approvals/${a.id}`, { decision: "allow_task" })],
           ["不行，换个办法", () => go(`/api/approvals/${a.id}`, { decision: "deny" })]];
    }
  }

  useEffect(() => {
    if (!first || askMulti) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || busy) return;
      const el = e.target as HTMLElement;
      const typing = (el?.tagName === "TEXTAREA" || el?.tagName === "INPUT") && (el as HTMLInputElement).value !== "";
      if (typing) return;  // 输入框里有字时，数字和回车照常打字、发送
      const k = Number(e.key);
      if (k >= 1 && k <= opts.length) { e.preventDefault(); opts[k - 1][1](); return; }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setSel((i) => (i + (e.key === "ArrowDown" ? 1 : opts.length - 1)) % opts.length);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  if (!first) return null;
  return (
    <div className="ask" role="dialog" aria-label={head}>
      <div className="q">
        <img src={face} alt="" />
        <span>{head}</span>
        {n > 1 && <span className="more">还有 {n - 1} 件</span>}
      </div>
      {body && <pre className="sel">{body}</pre>}
      {isAskUser && askMulti && ask ? (
        <>
          <div className="opts" role="listbox">
            {askChoices.map((c, i) => {
              const on = picked.includes(c);
              return (
                <button key={c} role="option" aria-selected={on} className={on ? "on" : ""} disabled={busy}
                  onClick={() => setPicked(on ? picked.filter((x) => x !== c) : [...picked, c])}>
                  <kbd>{on ? "✓" : i + 1}</kbd>{c}
                </button>
              );
            })}
          </div>
          <div className="acts" style={{ marginTop: 8 }}>
            <input className="input" style={{ flex: 1 }} value={custom} onChange={(e) => setCustom(e.target.value)}
              placeholder="也可以补一句自己的想法" />
            <button className="btn sm p" disabled={busy || (!picked.length && !custom.trim())}
              onClick={() => go(`/api/approvals/${ask.id}`, { decision: "allow", answer: [...picked, custom.trim()].filter(Boolean) })}>
              选好了
            </button>
            <button className="btn sm" disabled={busy} onClick={() => go(`/api/approvals/${ask.id}`, { decision: "deny" })}>你定吧</button>
          </div>
        </>
      ) : (
        <>
          <div className="opts" role="listbox">
            {opts.map(([label, run], i) => (
              <button key={label} role="option" aria-selected={i === sel} className={i === sel ? "on" : ""} disabled={busy}
                onMouseEnter={() => setSel(i)} onClick={run}>
                <kbd>{i + 1}</kbd>{label}
              </button>
            ))}
          </div>
          {isAskUser && ask && (
            <div className="acts" style={{ marginTop: 8 }}>
              <input className="input" style={{ flex: 1 }} value={custom} onChange={(e) => setCustom(e.target.value)}
                placeholder="或者自己写一句给队员"
                onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing && custom.trim()) go(`/api/approvals/${ask.id}`, { decision: "allow", answer: custom.trim() }); }} />
              <button className="btn sm p" disabled={busy || !custom.trim()}
                onClick={() => go(`/api/approvals/${ask.id}`, { decision: "allow", answer: custom.trim() })}>发送</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
