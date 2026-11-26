// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 数据层：/api/state 一次拿齐对话、事、待确认、产物；SSE 有动静就防抖 120 ms 重拉。
// 交互状态（菜单、浮层、确认）留在各自组件里，重拉只换数据。
import { useCallback, useEffect, useState } from "react";
import { api, streamUrl } from "./api";
import type { AppState, Settings, Worker } from "./types";

const EMPTY: AppState = { jobs: [], messages: [], decisions: [], approvals: [], artifacts: [], workspace: "", connectors: 0 };

export function useData(ready: boolean, onBrowser?: () => void) {
  const [state, setState] = useState<AppState>(EMPTY);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);

  const reload = useCallback(async () => {
    const [st, ws, se] = await Promise.all([
      api<AppState>("/api/state"),
      api<Worker[]>("/api/workers"),
      api<Settings>("/api/settings"),
    ]);
    setState(st);
    setWorkers(ws);
    setSettings(se);
  }, []);

  useEffect(() => {
    if (!ready) return;
    reload().catch(() => {});
    let timer: number | undefined;
    const es = new EventSource(streamUrl());
    es.onmessage = (ev) => {
      try {
        if (onBrowser && JSON.parse(ev.data)?.type === "browser") onBrowser();
      } catch { /* ignore */ }
      window.clearTimeout(timer);
      timer = window.setTimeout(() => reload().catch(() => {}), 120);
    };
    return () => {
      es.close();
      window.clearTimeout(timer);
    };
  }, [ready, reload, onBrowser]);

  return { state, workers, settings, reload };
}

export type Data = ReturnType<typeof useData>;
