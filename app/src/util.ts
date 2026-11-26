// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
import type { Intent, Job, Task } from "./types";

export const pct = (p: number | null | undefined) => `${Math.round((p || 0) * 100)}%`;
export const hm = (ms: number) => {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
export function when(ms: number): string {
  const d = new Date(ms);
  const now = new Date();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff === 0) return hm(ms);
  if (diff === 1) return "昨天";
  if (diff < 7) return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.getDay()];
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}
export function dayLabel(ms: number): string {
  const w = when(ms);
  return w.includes(":") ? `今天 ${hm(ms)}` : `${w} ${hm(ms)}`;
}
export function size(n: number): string {
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} 字节`;
}
export const CN: Record<Intent, string> = { augment: "补充", append: "追加", overturn: "推翻" };
export const INTENTS: Intent[] = ["augment", "append", "overturn"];
export const TERMINAL = ["done", "failed", "cancelled"];
export const isLive = (t: { status: string } | null | undefined) => !!t && !TERMINAL.includes(t.status);

export function statusText(t: Task): string {
  return (
    { running: "进行中", waiting: "排队", pending: "准备中", done: "已完成", failed: "中断了", cancelled: "已取消" }[
      t.status
    ] || t.status
  );
}

export function jobOf(jobs: Job[], taskId: string | null | undefined): Job | undefined {
  if (!taskId) return undefined;
  return jobs.find((j) => j.id === taskId || j.children.some((k) => k.id === taskId));
}

export function taskOf(jobs: Job[], taskId: string | null | undefined): Task | undefined {
  if (!taskId) return undefined;
  for (const j of jobs) {
    if (j.id === taskId) return j;
    const k = j.children.find((c) => c.id === taskId);
    if (k) return k;
  }
  return undefined;
}

export const KIND_LABEL: Record<string, string> = { doc: "MD", sheet: "表", slides: "PPT", other: "文件" };
const TONE: [RegExp, string][] = [
  [/\.(docx?|wps|rtf|odt)$/i, "word"], [/\.(xlsx?|xlsm|csv|tsv|ods)$/i, "excel"], [/\.(pptx?|key|odp)$/i, "ppt"],
  [/\.pdf$/i, "pdf"], [/\.(md|txt|markdown)$/i, "text"], [/\.html?$/i, "web"],
  [/\.(py|js|mjs|ts|tsx|jsx|sh|css|json|ya?ml|sql)$/i, "code"], [/\.(png|jpe?g|gif|webp|svg|bmp|heic)$/i, "img"],
];
/** 文件图标的颜色按扩展名：Word 蓝、Excel 绿、PPT 橙红、PDF 红…… */
export function fileTone(name: string, kind?: string): string {
  const hit = TONE.find(([re]) => re.test(name));
  return hit ? hit[1] : kind === "doc" ? "text" : "other";
}

export function fileBadge(name: string, kind: string): string {
  const ext = name.split(".").pop()?.toUpperCase() || "";
  return ext && ext.length <= 4 ? ext : KIND_LABEL[kind] || "文件";
}

/** 队员的工作笔记：「整理成果-报告撰写员.md」（末尾是队员名）或老的「A-整理成果.md」，交付时不单独列 */
export function isNote(name: string, workers: { name: string }[]): boolean {
  if (/^[A-Z]-/.test(name)) return true;
  const stem = name.replace(/\.[^.]+$/, "");
  const i = stem.lastIndexOf("-");
  if (i < 0) return false;
  const tail = stem.slice(i + 1);
  return workers.some((w) => w.name.replace(/ /g, "") === tail);
}
