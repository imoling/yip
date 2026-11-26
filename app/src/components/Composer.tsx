// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 输入框：照 Claude Code。Enter 发送，Shift+Enter 换行；输入法选字时的 Enter 不算发送。
// 左边「＋」：加文件或照片（⌘U，也能拖进来、粘贴）、工作文件夹、斜杠命令、连接器；右边「模式」：动手前要不要问你。
// 打「/」弹出命令列表，Tab 或 Enter 补全。
import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { api, rawUrl, uploadFile, type Upload } from "../api";
import { pickFolder } from "../tauri";
import type { Worker } from "../types";
import { size } from "../util";
import { avatarOf } from "./Avatar";
import { Icon } from "./Icon";

export interface ComposerHandle {
  focus: () => void;
  fill: (text: string) => void;
}

interface Props {
  sending: boolean;
  error: string;
  workspace: string;
  connectors: number;
  workers: Worker[];
  tray?: ReactNode;
  onSend: (text: string, attachments: number[]) => Promise<boolean>;
  onFolderChanged: () => void;
  onFolderSettings: () => void;
  onConnectors: () => void;
}

export type Mode = "auto" | "ask_writes" | "plan" | "allow_all";
export const MODES: [Mode, string, string][] = [
  ["auto", "自动", "工作文件夹里直接做；发出去、删东西、碰外面的才问"],
  ["ask_writes", "手动", "写文件、跑命令都先问"],
  ["plan", "计划", "拆完先给你看，你说开工再派"],
  ["allow_all", "全部放行", "除了发邮件，什么都不问"],
];
export const SLASH: [string, string][] = [
  ["/新事", "不插进正在做的事，另起一件"],
  ["/自动化", "到点自动做，比如每个工作日 9 点"],
  ["/记住", "记一条偏好或事实"],
  ["/停下", "停下正在做的事"],
  ["/计划", "这件事先给我看计划"],
  ["/清屏", "界面清空，聊天记录和 Yip 的记忆都还在"],
];

type Att = Upload & { local?: string; busy?: boolean; key: number };
type Menu = "" | "plus" | "folder" | "mode";
let seq = 0;
const home = (p: string) => p.replace(/^\/Users\/[^/]+/, "~");

export const Composer = forwardRef<ComposerHandle, Props>(function Composer(p, ref) {
  const [text, setText] = useState("");
  const [atts, setAtts] = useState<Att[]>([]);
  const [drag, setDrag] = useState(false);
  const [menu, setMenu] = useState<Menu>("");
  const [mode, setMode] = useState<Mode>("auto");
  const [pick, setPick] = useState(0);
  const [err, setErr] = useState("");
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileIn = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const mentionBox = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({
    focus: () => ta.current?.focus(),
    fill: (t: string) => {
      setText(t);
      requestAnimationFrame(() => ta.current?.focus());
    },
  }));

  useEffect(() => {
    api<{ yip: { approval: string } }>("/api/settings")
      .then((s) => setMode((s.yip.approval === "ask_all" ? "ask_writes" : s.yip.approval) as Mode))
      .catch(() => undefined);
  }, []);

  const fit = () => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };
  useLayoutEffect(fit, [text]);
  useEffect(() => {
    const ro = new ResizeObserver(fit);
    if (ta.current) ro.observe(ta.current.parentElement!);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setMenu(""); };
    const esc = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.key === "Escape") { e.preventDefault(); setMenu(""); ta.current?.focus(); }
      const n = Number(e.key);  // 模式菜单开着时按 1 到 4 直接选
      if (menu === "mode" && n >= 1 && n <= MODES.length) { e.preventDefault(); choose(MODES[n - 1][0]); }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [menu]);

  // 斜杠命令：开头是 / 且还没打空格时弹出；@ 队员：开头是 @ 且还没打空格时弹出
  const slash = /^\/\S*$/.test(text) ? SLASH.filter(([c]) => c.startsWith(text)) : [];
  const mentionQ = /^@(\S*)$/.exec(text)?.[1]?.toLowerCase();
  const mentions = mentionQ !== undefined
    ? p.workers.filter((w) => w.enabled && (w.name.toLowerCase().includes(mentionQ) || w.id.toLowerCase().includes(mentionQ)))
    : [];
  useEffect(() => setPick(0), [text]);
  useEffect(() => {
    const el = mentionBox.current?.children[pick] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [pick, mentions.length]);

  async function add(files: File[]) {
    setErr("");
    for (const f of files) {
      const key = seq++;
      const local = f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined;
      setAtts((a) => [...a, { key, id: -1, name: f.name || "图片", kind: local ? "image" : "file", size: f.size, mime: f.type, path: "", has_text: false, local, busy: true }]);
      try {
        const u = await uploadFile(f);
        setAtts((a) => a.map((x) => (x.key === key ? { ...x, ...u, busy: false } : x)));
      } catch (e) {
        setAtts((a) => a.filter((x) => x.key !== key));
        setErr(`${f.name} 没传上：${(e as Error).message}`);
      }
    }
  }

  async function send() {
    const t = text.trim();
    const ids = atts.filter((a) => !a.busy && a.id > 0).map((a) => a.id);
    if ((!t && !ids.length) || p.sending || atts.some((a) => a.busy)) return;
    setText("");
    const keep = atts;
    setAtts([]);
    const ok = await p.onSend(t, ids);
    if (!ok) {
      setText(t);
      setAtts(keep);
    }
    ta.current?.focus();
  }

  async function changeFolder() {
    setMenu("");
    const picked = await pickFolder();
    if (picked === undefined) return p.onFolderSettings();
    if (!picked) return;
    await api("/api/settings", { method: "PUT", body: { yip: { workspace: picked } } });
    p.onFolderChanged();
  }

  async function choose(m: Mode) {
    const old = mode;
    setMode(m);
    setMenu("");
    ta.current?.focus();
    try {
      await api("/api/settings", { method: "PUT", body: { yip: { approval: m } } });
    } catch (e) {
      setMode(old);
      setErr(`模式没切成：${(e as Error).message}`);
    }
  }

  function complete(cmd: string) {
    setText(`${cmd} `);
    requestAnimationFrame(() => ta.current?.focus());
  }

  const busy = atts.some((a) => a.busy);
  const cur = MODES.find(([m]) => m === mode) || MODES[0];
  return (
    <div className="composer">
      {(p.error || err) && <div className="cerr">{p.error || err}</div>}
      {p.tray}
      <div ref={box} className={`cbox ${drag ? "drag" : ""}`}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); add(Array.from(e.dataTransfer.files)); }}>
        {slash.length > 0 && (
          <div className="pmenu slashm" role="listbox" aria-label="斜杠命令">
            {slash.map(([c, d], i) => (
              <button key={c} role="option" aria-selected={i === pick} className={i === pick ? "on" : ""}
                onMouseDown={(e) => { e.preventDefault(); complete(c); }}>
                <code>{c}</code><small>{d}</small>
              </button>
            ))}
          </div>
        )}
        {mentions.length > 0 && (
          <div ref={mentionBox} className="pmenu mentionm" role="listbox" aria-label="指定队员">
            {mentions.map((w, i) => (
              <button key={w.id} role="option" aria-selected={i === pick} className={i === pick ? "on" : ""}
                onMouseDown={(e) => { e.preventDefault(); complete(`@${w.name}`); }}>
                <img src={avatarOf(w.id, p.workers)} alt="" />
                <code>@{w.name}</code>
                <small>{w.source === "external" ? "外部队员" : w.introduction}</small>
              </button>
            ))}
          </div>
        )}
        {(menu === "plus" || menu === "folder") && (
          <div className="pmenu plusm" role="menu">
            {menu === "plus" ? (
              <>
                <button role="menuitem" onClick={() => { setMenu(""); fileIn.current?.click(); }}>
                  <Icon name="paperclip" /><span className="t">添加文件或照片</span><kbd>⌘U</kbd>
                </button>
                <button role="menuitem" onClick={() => setMenu("folder")}>
                  <Icon name="folder" /><span className="t">工作文件夹<small>{home(p.workspace)}</small></span><Icon name="right" />
                </button>
                <button role="menuitem" onClick={() => { setMenu(""); complete("/"); setText("/"); }}>
                  <Icon name="slash" /><span className="t">斜杠命令</span>
                </button>
                <button role="menuitem" onClick={() => { setMenu(""); p.onConnectors(); }}>
                  <Icon name="plug" /><span className="t">连接器<small>{p.connectors ? `已接 ${p.connectors} 个` : "还没接"}</small></span><Icon name="right" />
                </button>
              </>
            ) : (
              <>
                <button role="menuitem" onClick={() => setMenu("plus")}><Icon name="back" /><span className="t">{home(p.workspace)}</span></button>
                <button role="menuitem" onClick={async () => { setMenu(""); await api("/api/workspace/reveal", { method: "POST" }); }}>
                  <Icon name="folder" /><span className="t">在访达中打开</span>
                </button>
                <button role="menuitem" onClick={changeFolder}><Icon name="swap" /><span className="t">换一个文件夹…</span></button>
              </>
            )}
          </div>
        )}
        {menu === "mode" && (
          <div className="pmenu modem" role="menu" aria-label="动手前要不要问你">
            <div className="h">动手前要不要问你</div>
            {MODES.map(([m, label, desc], i) => (
              <button key={m} role="menuitemradio" aria-checked={m === mode} onClick={() => choose(m)}>
                <span className="ck">{m === mode ? <Icon name="check" /> : null}</span>
                <span className="t">{label}<small>{desc}</small></span><kbd>{i + 1}</kbd>
              </button>
            ))}
          </div>
        )}
        {atts.length > 0 && (
          <div className="atts">
            {atts.map((a) => (
              <div key={a.key} className={`att ${a.kind}`} title={a.name}>
                {a.kind === "image" ? <img src={a.local || rawUrl(`/api/uploads/${a.id}/raw`)} alt="" />
                  : <span className="fi sm other">{a.name.split(".").pop()?.toUpperCase().slice(0, 4)}</span>}
                {a.kind === "file" && <span className="an"><b>{a.name}</b><small>{a.busy ? "正在传…" : size(a.size)}</small></span>}
                {a.busy && a.kind === "image" && <span className="ab"><span className="dots"><i /><i /><i /></span></span>}
                <button className="ax" aria-label="去掉" onClick={() => setAtts(atts.filter((x) => x.key !== a.key))}><Icon name="x" size={11} /></button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={ta}
          rows={1}
          value={text}
          autoFocus
          placeholder={drag ? "松手就加进来" : "跟 Yip 说点什么，输入 @ 可指定队员…"}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            const fs = Array.from(e.clipboardData.files);
            if (fs.length) { e.preventDefault(); add(fs); }
          }}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing || e.keyCode === 229) return;
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "u") {
              e.preventDefault();
              fileIn.current?.click();
              return;
            }
            if (slash.length) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setPick((i) => (i + (e.key === "ArrowDown" ? 1 : slash.length - 1)) % slash.length);
                return;
              }
              if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && text !== slash[pick][0])) {
                e.preventDefault();
                complete(slash[pick][0]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setText("");
                return;
              }
            }
            if (mentions.length) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setPick((i) => (i + (e.key === "ArrowDown" ? 1 : mentions.length - 1)) % mentions.length);
                return;
              }
              if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                e.preventDefault();
                complete(`@${mentions[pick].name}`);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setText("");
                return;
              }
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="row">
          <button className={`plus ${menu === "plus" || menu === "folder" ? "on" : ""}`} aria-label="加文件、换文件夹、斜杠命令、连接器"
            aria-expanded={menu === "plus"} onClick={() => setMenu(menu ? "" : "plus")}>
            <Icon name="plus" />
          </button>
          <input ref={fileIn} type="file" multiple hidden onChange={(e) => { add(Array.from(e.target.files || [])); e.target.value = ""; }} />
          <span className="sp" />
          <button className={`mode ${mode} ${menu === "mode" ? "on" : ""}`} aria-expanded={menu === "mode"} title={cur[2]}
            onClick={() => setMenu(menu === "mode" ? "" : "mode")}>
            <Icon name="shield" />{cur[1]}<Icon name="chev" />
          </button>
          <button className="go" disabled={(!text.trim() && !atts.length) || p.sending || busy} onClick={send} aria-label="发送">
            <Icon name="send" />
          </button>
        </div>
      </div>
    </div>
  );
});
