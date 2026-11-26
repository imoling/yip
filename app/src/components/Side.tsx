// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
import type { Settings } from "../types";
import { Icon } from "./Icon";
import icon from "../assets/icon.webp";

export type View = "chat" | "records" | "members" | "memory" | "automations" | "skills" | "connectors";

interface Props {
  view: View;
  waiting: number;
  settings: Settings | null;
  onView: (v: View) => void;
  onSettings: () => void;
}

const ITEMS: [View, string, string][] = [
  ["chat", "chat", "对话"],
  ["records", "list", "记录"],
  ["members", "team", "队员"],
  ["memory", "brain", "记忆"],
];
const MORE: [View, string, string][] = [
  ["automations", "timer", "跟进与定时"],
  ["skills", "skill", "技能"],
  ["connectors", "plug", "连接器"],
];

export function Side({ view, waiting, settings, onView, onSettings }: Props) {
  const st = settings?.status;
  const btn = ([v, ic, label]: [View, string, string]) => (
    <button key={v} className={`nb ${view === v ? "on" : ""}`} onClick={() => onView(v)} title={label} aria-label={label}>
      <Icon name={ic} />
      {v === "chat" && waiting > 0 && <span className="badge">{waiting}</span>}
    </button>
  );
  return (
    <aside className="side" data-tauri-drag-region>
      <div className="logo" data-tauri-drag-region>
        <img src={icon} alt="" />
        <b>Yip</b>
      </div>
      {ITEMS.map(btn)}
      <div className="sep" />
      {MORE.map(btn)}
      <span className="sp" data-tauri-drag-region />
      <button className="nb" onClick={onSettings} title="设置" aria-label="设置">
        <Icon name="gear" />
      </button>
      <button
        className={`mdl ${st?.state === "ok" ? "ok" : st?.state === "error" ? "error" : ""}`}
        onClick={onSettings}
        title={!st || st.state === "off" ? "没配模型，按规则判断" : `${settings?.model_name || ""} · ${st.text}`}
        aria-label="模型状态"
      >
        <i />
      </button>
    </aside>
  );
}
