// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 壳里才有 window.__TAURI__（tauri.conf.json 开了 withGlobalTauri）；浏览器里这些都是空操作。

type Tauri = {
  core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  event: { listen: (name: string, cb: () => void) => Promise<() => void> };
};

const T = (window as unknown as { __TAURI__?: Tauri }).__TAURI__;

export const inShell = !!T;

/** 壳要关窗时回调；返回取消监听的函数 */
export function onCloseRequested(cb: () => void): () => void {
  if (!T) return () => {};
  let off: (() => void) | null = null;
  let dead = false;
  T.event.listen("yip:close-requested", cb).then((f) => (dead ? f() : (off = f)));
  return () => {
    dead = true;
    off?.();
  };
}

export function confirmClose() {
  T?.core.invoke("confirm_close");
}

/** 原生选文件夹；浏览器里返回 undefined，由调用方退回到输入框 */
export async function pickFolder(): Promise<string | null | undefined> {
  const d = (window as unknown as { __TAURI__?: { dialog?: { open: (o: Record<string, unknown>) => Promise<string | null> } } }).__TAURI__?.dialog;
  if (!d) return undefined;
  return d.open({ directory: true, multiple: false, title: "选一个工作文件夹" });
}

type Notif = {
  isPermissionGranted: () => Promise<boolean>;
  requestPermission: () => Promise<string>;
  sendNotification: (o: { title: string; body: string }) => void;
};

/** 系统通知：只在壳里、而且窗口没在前台时发 */
export async function notify(title: string, body: string): Promise<void> {
  const n = (window as unknown as { __TAURI__?: { notification?: Notif } }).__TAURI__?.notification;
  if (!n || document.hasFocus()) return;
  let ok = await n.isPermissionGranted();
  if (!ok) ok = (await n.requestPermission()) === "granted";
  if (ok) n.sendNotification({ title, body });
}
