// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 布局：左侧图标栏 + 主区（对话 / 记录 / 队员 / 技能 / 连接器）+ 对话页右栏（进度｜产物）。
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useData } from "./store";
import { inShell, notify } from "./tauri";
import type { Artifact } from "./types";
import { Automations } from "./components/Automations";
import { Records } from "./components/Records";
import { Boot } from "./components/Boot";
import { Chat } from "./components/Chat";
import type { ComposerHandle } from "./components/Composer";
import { BrowserPane } from "./components/BrowserPane";
import { Connectors } from "./components/Connectors";
import { FilePane } from "./components/FilePane";
import { MemberPane } from "./components/MemberPane";
import { History } from "./components/History";
import { Members } from "./components/Members";
import { Memory } from "./components/Memory";
import { Rail } from "./components/Rail";
import { SettingsSheet } from "./components/Settings";
import { Side, type View } from "./components/Side";
import { Skills } from "./components/Skills";

export function App() {
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<View>("chat");
  const [railOpen, setRailOpen] = useState(() => localStorage.getItem("yip.rail") !== "0");
  const [settingsTab, setSettingsTab] = useState<null | "general" | "model" | "about">(null);
  const [file, setFile] = useState<Artifact | null>(null);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [member, setMember] = useState<string | null>(null);  // 右栏在看哪个队员的进展
  const onBrowserEvent = useCallback(() => {
    setFile(null);
    setMember(null);
    setBrowserOpen(true);
  }, []);
  const { state, workers, settings, reload } = useData(ready, onBrowserEvent);
  const [history, setHistory] = useState(false);
  const [clearedAt, setClearedAt] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  const composer = useRef<ComposerHandle>(null);
  const onReady = useCallback(() => setReady(true), []);
  const refresh = useCallback(() => { reload().catch(() => {}); }, [reload]);

  // Yip 回闲聊是异步的：最后一条是你说的话、又没有正在派的活时，显示「正在想…」
  useEffect(() => {
    const last = state.messages[state.messages.length - 1];
    setThinking(!!last && last.role === "user" && last.task_id === null);
  }, [state.messages]);

  // 关窗只是藏到菜单栏，活照常干；要我确认的事多了，窗口不在前台时发通知
  const waitingNow = state.approvals.length + state.decisions.filter((d) => d.status === "pending_confirm").length
    + state.jobs.filter((j) => j.status === "running" && j.children.length === 0 && j.note === "等你点头").length;
  const lastWaiting = useRef(0);
  useEffect(() => {
    if (waitingNow > lastWaiting.current) notify(state.name || "Yip", `有 ${waitingNow} 件事等你确认`);
    lastWaiting.current = waitingNow;
  }, [waitingNow, state.name]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !(e.metaKey || e.ctrlKey)) return;
      if (e.key === ",") {
        e.preventDefault();
        setSettingsTab("general");
      } else if (e.key === "1") {
        e.preventDefault();
        setView("chat");
      } else if (e.key === "2") {
        e.preventDefault();
        setView("records");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!ready) return <Boot onReady={onReady} />;

  async function send(text: string, attachments: number[] = []): Promise<boolean> {
    setSending(true);
    setErr("");
    setPending(text || "（附件）");
    try {
      await api("/api/say", { body: { text, attachments } });
      await reload();
      return true;
    } catch (e) {
      setErr(`发送失败：${(e as Error).message}`);
      return false;
    } finally {
      setPending(null);
      setSending(false);
    }
  }

  const waiting = state.approvals.length + state.decisions.filter((d) => d.status === "pending_confirm").length;
  const toggleRail = () => {
    localStorage.setItem("yip.rail", railOpen ? "0" : "1");
    setRailOpen(!railOpen);
  };
  const showRail = view === "chat" && railOpen;
  const showFile = view === "chat" && !!file;
  const showBrowser = view === "chat" && !file && browserOpen;
  const showWideRight = showFile || showBrowser;

  return (
    <div className={`win ${showWideRight ? "doc" : showRail ? "" : "wide"}`}>
      <Side view={view} waiting={waiting} settings={settings} onView={setView}
        onSettings={() => setSettingsTab(settings?.configured ? "general" : "model")} />
      {view === "chat" && (
        <Chat ref={composer} state={state} workers={workers} pending={pending} thinking={thinking && !pending} sending={sending}
          error={err} railOpen={railOpen} onToggleRail={toggleRail}
          browserOpen={showBrowser} onToggleBrowser={() => { setFile(null); setBrowserOpen((v) => !v); }}
          onSend={send}
          onOpenFile={(a) => { setBrowserOpen(false); setFile(a); }}
          onConnectors={() => setView("connectors")} onMemory={() => setView("memory")} onAutomations={() => setView("automations")} onMember={(id) => { setMember(id); setFile(null); setBrowserOpen(false); if (!railOpen) toggleRail(); }}
          onHistory={() => setHistory(true)}
          clearedAt={clearedAt}
          onClear={() => {  // 点了就清，前情在后台写
            setClearedAt(Math.max(0, ...state.messages.map((m) => m.id)));
            api("/api/chat/clear", { body: {} }).then(refresh).catch((e) => { setClearedAt(0); setErr(`没清成：${(e as Error).message}`); });
          }}
          onFolder={() => setSettingsTab("general")} onFolderChanged={refresh}
          onError={setErr} onChanged={refresh} />
      )}
      {view === "records" && <Records workers={workers} />}
      {view === "members" && <Members workers={workers} configured={!!settings?.configured} onChanged={refresh} />}
      {view === "memory" && <Memory />}
      {view === "automations" && <Automations inShell={inShell} onOpenChat={() => setView("chat")} />}
      {view === "skills" && <Skills workers={workers} configured={!!settings?.configured} />}
      {view === "connectors" && <Connectors onChanged={refresh} />}
      {showFile && file && <FilePane a={file} onClose={() => setFile(null)} />}
      {showBrowser && <BrowserPane onClose={() => setBrowserOpen(false)} onError={setErr} />}
      {showRail && !showWideRight && member && view === "chat" && (
        <MemberPane taskId={member} state={state} workers={workers} onClose={() => setMember(null)} onOpenFile={(a) => { setBrowserOpen(false); setFile(a); }} />
      )}
      {showRail && !showWideRight && !(member && view === "chat") && (
        <Rail state={state} workers={workers} onRecords={() => setView("records")} onOpenFile={(a) => { setBrowserOpen(false); setFile(a); }} onError={setErr} onChanged={refresh} onMember={setMember} />
      )}
      {history && <History name={state.name || "Yip"} onClose={() => setHistory(false)} onOpenFile={(a) => { setHistory(false); setView("chat"); setFile(a); }} />}
      {settingsTab && settings && (
        <SettingsSheet settings={settings} initial={settingsTab} onClose={() => setSettingsTab(null)} onSaved={refresh} />
      )}
    </div>
  );
}
