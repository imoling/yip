// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 右侧实时浏览器工作区：CDP Screencast 实时画面 + 鼠键双向操控 + 队员动作可视化叠加与一键停下。
import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "../api";
import { Icon } from "./Icon";

interface TabItem {
  key: string;
  url: string;
  title: string;
  active: boolean;
  is_ai: boolean;
}

interface ActionState {
  busy: boolean;
  label: string;
  box?: { x: number; y: number; w: number; h: number } | null;
  task_id?: string | null;
}

interface BrowserState {
  open: boolean;
  url: string;
  title: string;
  headed: boolean;
  who: string;
  viewport: { w: number; h: number };
  tabs: TabItem[];
  action: ActionState | null;
}

const SPECIAL_KEYS = new Set([
  "Enter", "Backspace", "Delete", "Tab", "Escape",
  "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
  "Home", "End", "PageUp", "PageDown",
]);

export function BrowserPane({ onClose, onError }: { onClose: () => void; onError: (m: string) => void }) {
  const [st, setSt] = useState<BrowserState>({
    open: false, url: "", title: "", headed: false, who: "",
    viewport: { w: 1280, h: 800 }, tabs: [], action: null,
  });
  const [addr, setAddr] = useState("");
  const [editing, setEditing] = useState(false);
  const [connected, setConnected] = useState(false);
  const [hasFrame, setHasFrame] = useState(false);
  const [loading, setLoading] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imeRef = useRef<HTMLInputElement>(null);
  const composingRef = useRef(false);
  const vpRef = useRef({ w: 1280, h: 800 });
  const editingRef = useRef(false);
  editingRef.current = editing;

  function send(msg: Record<string, unknown>) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  useEffect(() => {
    let alive = true;
    let retryTimer: number | undefined;

    function connect() {
      if (!alive) return;
      const ws = new WebSocket(wsUrl("/api/browser/ws"));
      wsRef.current = ws;
      ws.onopen = () => {
        if (!alive) return;
        setConnected(true);
        const el = stageRef.current;
        if (el && el.clientWidth > 100 && el.clientHeight > 100) {
          ws.send(JSON.stringify({ type: "resize", w: Math.round(el.clientWidth), h: Math.round(el.clientHeight) }));
        }
      };
      ws.onmessage = (ev) => {
        if (!alive) return;
        let m: any;
        try { m = JSON.parse(ev.data); } catch { return; }
        if (m.type === "frame" && m.data) {
          const w = m.w || vpRef.current.w;
          const h = m.h || vpRef.current.h;
          vpRef.current = { w, h };
          const img = new Image();
          img.onload = () => {
            const c = canvasRef.current;
            if (!c) return;
            if (c.width !== img.naturalWidth || c.height !== img.naturalHeight) {
              c.width = img.naturalWidth;
              c.height = img.naturalHeight;
            }
            const ctx = c.getContext("2d");
            if (ctx) ctx.drawImage(img, 0, 0);
            setHasFrame(true);
            setLoading(false);
          };
          img.src = `data:image/jpeg;base64,${m.data}`;
        } else if (m.type === "state" && m.data) {
          const d = m.data as BrowserState;
          setSt(d);
          if (d.viewport) vpRef.current = d.viewport;
          if (!editingRef.current) setAddr(d.url || "");
          if (!d.open) setHasFrame(false);
        } else if (m.type === "action") {
          setSt((prev) => ({ ...prev, action: m.data || null }));
        } else if (m.type === "error" && m.message) {
          setLoading(false);
          onError(m.message);
        }
      };
      ws.onclose = () => {
        if (!alive) return;
        setConnected(false);
        retryTimer = window.setTimeout(connect, 1500);
      };
    }

    connect();
    return () => {
      alive = false;
      window.clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [onError]);

  // 跟随容器宽度同步浏览器视口，避免缩放发糊或裁切
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    let timer: number | undefined;
    const ro = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (el.clientWidth > 200 && el.clientHeight > 200) {
          send({ type: "resize", w: Math.round(el.clientWidth), h: Math.round(el.clientHeight) });
        }
      }, 260);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      window.clearTimeout(timer);
    };
  }, []);

  function coords(e: React.MouseEvent<HTMLCanvasElement>): [number, number] | null {
    const c = canvasRef.current;
    if (!c) return null;
    const r = c.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const x = ((e.clientX - r.left) / r.width) * vpRef.current.w;
    const y = ((e.clientY - r.top) / r.height) * vpRef.current.h;
    return [Math.round(x), Math.round(y)];
  }

  function btnName(b: number): string {
    return b === 2 ? "right" : b === 1 ? "middle" : "left";
  }

  function openUrl(raw: string) {
    const u = raw.trim();
    if (!u) return;
    setLoading(true);
    setEditing(false);
    send({ type: "navigate", url: u });
  }

  async function toggleHeaded() {
    try {
      const next = !st.headed;
      const d = await api<BrowserState>("/api/browser/show", { body: { show: next } });
      setSt(d);
    } catch (e) {
      onError(`切换失败：${(e as Error).message}`);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (composingRef.current) return;
    const meta = e.metaKey || e.ctrlKey;
    if (meta && ["a", "c", "v", "x", "z"].includes(e.key.toLowerCase())) {
      e.preventDefault();
      send({ type: "key", key: `${e.metaKey ? "Meta" : "Control"}+${e.key.toLowerCase()}` });
      return;
    }
    if (SPECIAL_KEYS.has(e.key)) {
      e.preventDefault();
      send({ type: "key", key: e.key });
    }
  }

  const action = st.action;
  const box = action?.box;
  const boxStyle = box && vpRef.current.w > 0 && vpRef.current.h > 0 ? {
    left: `${(box.x / vpRef.current.w) * 100}%`,
    top: `${(box.y / vpRef.current.h) * 100}%`,
    width: `${(box.w / vpRef.current.w) * 100}%`,
    height: `${(box.h / vpRef.current.h) * 100}%`,
  } : undefined;

  return (
    <aside className="rail bpane">
      <div className="btabs">
        <div className="btab-list">
          {st.tabs.map((t) => (
            <div key={t.key} className={`btab ${t.active ? "on" : ""}`} onClick={() => send({ type: "switch_tab", key: t.key })} title={t.url || t.title}>
              {t.is_ai && <i className="bdot" />}
              <span>{t.title || "新标签页"}</span>
              <button className="bclose" onClick={(e) => { e.stopPropagation(); send({ type: "close_tab", key: t.key }); }} aria-label="关闭标签页">
                <Icon name="x" size={11} />
              </button>
            </div>
          ))}
          <button className="icon-btn bnew" onClick={() => send({ type: "new_tab" })} title="新建标签页">
            <Icon name="plus" size={14} />
          </button>
        </div>
        <button className="icon-btn" onClick={onClose} title="收起浏览器">
          <Icon name="x" size={15} />
        </button>
      </div>

      <form className="bbar" onSubmit={(e) => { e.preventDefault(); openUrl(addr); }}>
        <button type="button" className="icon-btn" disabled={!st.open} onClick={() => send({ type: "back" })} title="后退">
          <Icon name="back" size={15} />
        </button>
        <button type="button" className="icon-btn" disabled={!st.open} onClick={() => send({ type: "forward" })} title="前进">
          <Icon name="right" size={15} />
        </button>
        <button type="button" className="icon-btn" disabled={!st.open} onClick={() => { setLoading(true); send({ type: "reload" }); }} title="刷新">
          <Icon name="restart" size={14} />
        </button>
        <input
          className="burl"
          value={addr}
          placeholder="输入或粘贴网页地址，回车打开"
          onFocus={(e) => { setEditing(true); e.currentTarget.select(); }}
          onBlur={() => setEditing(false)}
          onChange={(e) => setAddr(e.target.value)}
        />
        <button
          type="button"
          className={`icon-btn ${st.headed ? "on" : ""}`}
          onClick={toggleHeaded}
          title={st.headed ? "收回后台" : "在 Chrome 窗口打开"}
        >
          <Icon name="external" size={14} />
        </button>
      </form>

      <div className="bstage" ref={stageRef}>
        {st.open ? (
          <>
            <canvas
              ref={canvasRef}
              className="bcanvas"
              onContextMenu={(e) => e.preventDefault()}
              onMouseDown={(e) => {
                imeRef.current?.focus();
                const pt = coords(e);
                if (pt) send({ type: "mouse", kind: "down", x: pt[0], y: pt[1], button: btnName(e.button) });
              }}
              onMouseUp={(e) => {
                const pt = coords(e);
                if (pt) send({ type: "mouse", kind: "up", x: pt[0], y: pt[1], button: btnName(e.button) });
              }}
              onDoubleClick={(e) => {
                const pt = coords(e);
                if (pt) send({ type: "mouse", kind: "dblclick", x: pt[0], y: pt[1], button: btnName(e.button) });
              }}
              onWheel={(e) => {
                const pt = coords(e);
                if (pt) send({ type: "mouse", kind: "wheel", x: pt[0], y: pt[1], dx: Math.round(e.deltaX), dy: Math.round(e.deltaY) });
              }}
            />
            <input
              ref={imeRef}
              className="bime"
              aria-label="网页输入"
              onKeyDown={handleKeyDown}
              onCompositionStart={() => { composingRef.current = true; }}
              onCompositionEnd={(e) => {
                composingRef.current = false;
                const val = e.currentTarget.value;
                e.currentTarget.value = "";
                if (val) send({ type: "text", text: val });
              }}
              onInput={(e) => {
                if (composingRef.current) return;
                const val = e.currentTarget.value;
                e.currentTarget.value = "";
                if (val) send({ type: "text", text: val });
              }}
            />
            {(!hasFrame || loading) && (
              <div className="bmask">{connected ? "正在打开…" : "正在连接…"}</div>
            )}
            {boxStyle && <div className="bhl" style={boxStyle} />}
            {action?.label && (
              <div className="bcap">
                <i className="bdot" />
                <span>{action.label}</span>
              </div>
            )}
            {action?.busy && (
              <button className="bstop" onClick={() => send({ type: "stop" })}>
                <i />停下
              </button>
            )}
          </>
        ) : (
          <div className="bempty">
            <Icon name="globe" size={28} />
            <form onSubmit={(e) => { e.preventDefault(); openUrl(addr); }}>
              <input
                className="input"
                value={addr}
                placeholder="粘贴网址回车打开，或直接让 Yip 去查网页"
                onChange={(e) => setAddr(e.target.value)}
              />
            </form>
          </div>
        )}
      </div>
    </aside>
  );
}
