// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 品牌标：模型服务用 lobe-icons（MIT，素材在 assets/logos/），连接器用 simple-icons（CC0）；
// 没有品牌标的用同一套线性图标配淡色底，保证一排卡片看着是一家。
// 邮箱：simple-icons 里有的（QQ、Gmail、阿里云）用原标；没有的（网易、Outlook、腾讯企业邮）用品牌色配字或信封，不仿画别家的标。
import { siAlibabacloud, siBrave, siGit, siGithub, siGmail, siNotion, siQq, siSqlite } from "simple-icons";
import { Icon } from "./Icon";

const files = import.meta.glob("../assets/logos/*.svg", { eager: true, import: "default" }) as Record<string, string>;
const PROVIDER: Record<string, string> = Object.fromEntries(
  Object.entries(files).map(([p, url]) => [p.split("/").pop()!.replace(".svg", ""), url]),
);
const MONO = new Set(["openai", "ollama"]);

export function ProviderLogo({ id, size = 28 }: { id: string; size?: number }) {
  const src = PROVIDER[id];
  return (
    <span className="logo-tile" style={{ width: size + 12, height: size + 12 }}>
      {src ? <img src={src} alt="" className={MONO.has(id) ? "mono-logo" : ""} style={{ width: size, height: size }} />
        : <Icon name="cpu" size={size - 6} />}
    </span>
  );
}

const BRAND: Record<string, { path: string; hex: string }> = {
  github: siGithub, notion: siNotion, git: siGit, sqlite: siSqlite, brave: siBrave,
};
const LINE: Record<string, [string, string]> = {
  filesystem: ["folder", "#2f7d5b"], fetch: ["globe", "#3b82b6"], playwright: ["window", "#2ead33"],
  time: ["clock", "#c48a1b"], amap: ["pin", "#1677ff"], lark: ["send", "#3370ff"], context7: ["doc", "#6b7280"],
};

export function ConnectorLogo({ id, size = 26 }: { id?: string; size?: number }) {
  const b = id ? BRAND[id] : undefined;
  const l = id ? LINE[id] : undefined;
  const color = b ? `#${b.hex}` : l ? l[1] : "var(--accent)";
  return (
    <span className="logo-tile" style={{ width: size + 14, height: size + 14, background: `color-mix(in srgb, ${color} 13%, var(--card))`, color }}>
      {b ? (
        <svg viewBox="0 0 24 24" width={size - 4} height={size - 4} fill="currentColor" aria-hidden="true" className={b === siGithub || b === siNotion ? "ink-logo" : ""}><path d={b.path} /></svg>
      ) : (
        <Icon name={l ? l[0] : "plug"} size={size - 4} />
      )}
    </span>
  );
}

const MAIL: Record<string, { brand?: { path: string }; text?: string; color: string }> = {
  qq: { brand: siQq, color: "#1EBAFC" },
  gmail: { brand: siGmail, color: "#EA4335" },
  aliyun: { brand: siAlibabacloud, color: "#FF6A00" },
  "163": { text: "163", color: "#D7000F" },
  outlook: { text: "O", color: "#0078D4" },
  exmail: { color: "#0052D9" },
};

export function MailLogo({ id, size = 22 }: { id: string; size?: number }) {
  const m = MAIL[id];
  const color = m?.color || "var(--accent)";
  return (
    <span className="logo-tile" style={{ width: size + 12, height: size + 12, background: `color-mix(in srgb, ${color} 13%, var(--card))`, color }}>
      {m?.brand ? (
        <svg viewBox="0 0 24 24" width={size - 2} height={size - 2} fill="currentColor" aria-hidden="true"><path d={m.brand.path} /></svg>
      ) : m?.text ? (
        <b style={{ fontSize: m.text.length > 1 ? size * 0.5 : size * 0.72, fontWeight: 800, letterSpacing: -0.3 }}>{m.text}</b>
      ) : (
        <Icon name="mail" size={size - 4} />
      )}
    </span>
  );
}
