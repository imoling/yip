// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 线性图标，1.6 描边，和视觉稿 docs/视觉对比/v4-单对话.html 同一套。

const P: Record<string, JSX.Element> = {
  chat: <path d="M5 18.5V7a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H8.5z" />,
  board: (
    <>
      <rect x="3.5" y="4" width="5" height="16" rx="1.5" />
      <rect x="10" y="4" width="5" height="10" rx="1.5" />
      <rect x="16.5" y="4" width="4" height="13" rx="1.5" />
    </>
  ),
  list: (
    <>
      <path d="M9 6.5h10M9 12h10M9 17.5h10" />
      <path d="M4.5 6.5l1 1 1.8-2M4.5 12l1 1 1.8-2M4.5 17.5l1 1 1.8-2" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 19.5c1-3.6 3.8-5.5 7-5.5s6 1.9 7 5.5" />
    </>
  ),
  team: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5" />
      <circle cx="17" cy="9" r="2.4" />
      <path d="M16.2 14.4c2 .2 3.6 1.5 4.3 4" />
    </>
  ),
  skill: <path d="M12 3l2.4 5 5.6.8-4 3.9.9 5.5L12 15.6 7.1 18.2l.9-5.5-4-3.9 5.6-.8z" />,
  plug: <path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5" />,
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" />
    </>
  ),
  send: <path d="M12 19V5M6 11l6-6 6 6" />,
  merge: <path d="M4 19h16M12 4v10M8 10l4 4 4-4" />,
  queue: <path d="M4 6h11M4 11h11M4 16h6M17 14v7M13.5 17.5h7" />,
  restart: (
    <>
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" />
      <path d="M4.5 4v4.5H9" />
    </>
  ),
  doc: (
    <>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4M10 12h5M10 16h5" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.5 2.6 2.5 14.4 0 17M12 3.5c-2.5 2.6-2.5 14.4 0 17" />
    </>
  ),
  pen: <path d="M4 20l4-1 11-11-3-3L5 16z" />,
  terminal: <path d="M4 6l5 5-5 5M11 18h9" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-4.5-4.5" />
    </>
  ),
  back: <path d="M15 5l-7 7 7 7" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />,
  folder: <path d="M3.5 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  rail: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="3" />
      <path d="M14.5 4v16" />
    </>
  ),
  link: <path d="M10 14l4-4M8.5 11.5l-2 2a3 3 0 0 0 4 4l2-2M15.5 12.5l2-2a3 3 0 0 0-4-4l-2 2" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  slash: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M14 8l-4 8" />
    </>
  ),
  shield: <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" />,
  chev: <path d="M7 10l5 5 5-5" />,
  history: (
    <>
      <path d="M5 18.5V7a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H8.5z" />
      <path d="M9 8.5h6M9 11.5h4" />
    </>
  ),
  eraser: <path d="M8 19h11M5.5 15.5l7.8-7.8a2 2 0 0 1 2.8 0l1.2 1.2a2 2 0 0 1 0 2.8L11.5 17.5a2 2 0 0 1-1.4.5H8.4a2 2 0 0 1-1.4-.6l-1.5-1.5a1.4 1.4 0 0 1 0-1.9z" />,
  right: <path d="M10 7l5 5-5 5" />,
  check: <path d="M5 12.5l4.5 4.5L19 7" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  split: <path d="M5 5h6v6H5zM13 13h6v6h-6zM11 8h4a2 2 0 0 1 2 2v3" />,
  swap: <path d="M7 7h11l-3-3M17 17H6l3 3" />,
  spark: <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5M12 8h.01" />
    </>
  ),
  cpu: (
    <>
      <rect x="7" y="7" width="10" height="10" rx="2" />
      <path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3" />
    </>
  ),
  upload: <path d="M12 16V4M7 9l5-5 5 5M5 20h14" />,
  trash: <path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13" />,
  play: <path d="M8 5l11 7-11 7z" />,
  bookmark: <path d="M7 4h10v16l-5-3.5L7 20z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l2.5 1.5" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z" />
      <circle cx="12" cy="10" r="2.2" />
    </>
  ),
  window: (
    <>
      <rect x="3.5" y="5" width="17" height="14" rx="2.5" />
      <path d="M3.5 9h17M7 7h.01M9.5 7h.01" />
    </>
  ),
  paperclip: <path d="M20 11.5l-7.8 7.8a5 5 0 0 1-7-7l8-8a3.3 3.3 0 0 1 4.7 4.7l-8 8a1.6 1.6 0 0 1-2.3-2.3l7.3-7.3" />,
  mail: (
    <>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2.5" />
      <path d="M4 7l8 6 8-6" />
    </>
  ),
  timer: (
    <>
      <circle cx="12" cy="13" r="7.5" />
      <path d="M12 9.5V13l2.5 2M9.5 3h5" />
    </>
  ),
  image: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M20.5 16l-5-5-8 8" />
    </>
  ),
  brain: (
    <>
      <path d="M9.5 4.5a3 3 0 0 0-3 3v.3a3 3 0 0 0-1.5 5.4 3 3 0 0 0 2.2 4.3 2.6 2.6 0 0 0 4.8.5V6.5a2.5 2.5 0 0 0-2.5-2z" />
      <path d="M14.5 4.5a3 3 0 0 1 3 3v.3a3 3 0 0 1 1.5 5.4 3 3 0 0 1-2.2 4.3 2.6 2.6 0 0 1-4.8.5" />
    </>
  ),
  edit: <path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3zM14 7.5l3 3" />,
  copy: (
    <>
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
    </>
  ),
};

export function Icon({ name, size }: { name: string; size?: number }) {
  return (
    <svg className="ic" viewBox="0 0 24 24" style={size ? { width: size, height: size } : undefined} aria-hidden="true">
      {P[name] || P.spark}
    </svg>
  );
}

/** 「在做什么」那句话 → 图标 */
export function noteIcon(note: string): string {
  if (/^搜索/.test(note)) return "search";
  if (/^读网页/.test(note)) return "globe";
  if (/^(写入|修改)|允许：写入/.test(note)) return "pen";
  if (/^(读取|看文件夹|看技能)/.test(note)) return "doc";
  if (/^(执行|运行技能)|允许：执行/.test(note)) return "terminal";
  if (/·/.test(note) && !/^等/.test(note)) return "plug";
  return "spark";
}

/** 工具名 → 图标 */
export function toolIcon(tool?: string): string {
  if (!tool) return "spark";
  if (tool.startsWith("web_search")) return "search";
  if (tool.startsWith("web_fetch")) return "globe";
  if (tool.includes("write") || tool.includes("edit")) return "pen";
  if (tool.includes("read") || tool.includes("list")) return "doc";
  if (tool.includes("shell") || tool.startsWith("skill__")) return "terminal";
  if (tool.startsWith("mcp__")) return "plug";
  return "spark";
}
