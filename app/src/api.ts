// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 后端地址和令牌：在 Tauri 壳里由 lib.rs 注入 window.__SA_HTTP__ / __SA_TOKEN__；
// 浏览器里（FastAPI 托管或 vite 代理）走同源，不带令牌。

declare global {
  interface Window {
    __SA_HTTP__?: string;
    __SA_TOKEN__?: string;
    __SA_PLATFORM__?: string;
    __SA_LOG__?: string;
  }
}

export const BASE = window.__SA_HTTP__ || "";
const TOKEN = window.__SA_TOKEN__ || "";

function headers(json: boolean): Record<string, string> {
  const h: Record<string, string> = {};
  if (json) h["content-type"] = "application/json";
  if (TOKEN) h.authorization = `Bearer ${TOKEN}`;
  return h;
}

export async function api<T = any>(path: string, opt: { method?: string; body?: unknown } = {}): Promise<T> {
  const form = opt.body instanceof FormData;
  const init: RequestInit = {
    method: opt.method || (opt.body !== undefined ? "POST" : "GET"),
    headers: headers(opt.body !== undefined && !form),
  };
  if (opt.body !== undefined) init.body = form ? (opt.body as FormData) : JSON.stringify(opt.body);
  let r: Response;
  try {
    r = await fetch(BASE + path, init);
  } catch {
    throw new Error("连不上后台");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { detail?: string }).detail || `请求失败：${r.status}`);
  return data as T;
}

export async function apiText(path: string): Promise<string> {
  const r = await fetch(BASE + path, { headers: headers(false) });
  if (!r.ok) throw new Error(r.status === 404 ? "文档不存在" : `请求失败：${r.status}`);
  return r.text();
}

export function streamUrl(): string {
  return `${BASE}/api/stream${TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : ""}`;
}

export function wsUrl(path: string): string {
  const base = BASE
    ? BASE.replace(/^http/i, "ws")
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
  return `${base}${path}${TOKEN ? `${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}` : ""}`;
}

export async function healthy(): Promise<boolean> {
  try {
    const r = await fetch(`${BASE}/api/health`, { headers: headers(false) });
    return r.ok;
  } catch {
    return false;
  }
}

export async function revealFile(id: number, open = false): Promise<void> {
  await api(`/api/artifacts/${id}/reveal`, { body: { open } });
}

/** 右栏里跑网页产物：令牌放路径里，网页引用的相对路径也带得上 */
export function viewUrl(id: number, name: string): string {
  return `${BASE}/view/${encodeURIComponent(TOKEN || "-")}/${id}/${encodeURIComponent(name)}`;
}

export function rawUrl(path: string): string {
  return `${BASE}${path}${TOKEN ? `${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}` : ""}`;
}

export interface Upload {
  id: number;
  name: string;
  kind: "image" | "file";
  size: number;
  mime: string;
  path: string;
  has_text: boolean;
}

export async function uploadFile(f: File): Promise<Upload> {
  const fd = new FormData();
  fd.append("file", f, f.name || "粘贴的图片.png");
  return api<Upload>("/api/uploads", { method: "POST", body: fd });
}
