// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 后台没就绪前的等待页：每 400 ms 问一次 /api/health，20 秒还没起来就说日志在哪。
import { useEffect, useState } from "react";
import { healthy } from "../api";
import icon from "../assets/icon.webp";

export function Boot({ onReady }: { onReady: () => void }) {
  const [slow, setSlow] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let alive = true;
    const t0 = Date.now();
    (async () => {
      while (alive) {
        if (await healthy()) return alive && onReady();
        if (Date.now() - t0 > 20000) setSlow(true);
        await new Promise((r) => setTimeout(r, 400));
      }
    })();
    return () => {
      alive = false;
    };
  }, [onReady, round]);
  return (
    <div className="boot" data-tauri-drag-region>
      <div>
        <img src={icon} alt="" />
        {slow ? (
          <>
            <p>后台没起来：多半是端口被占或文件不全</p>
            {window.__SA_LOG__ && (
              <p>
                日志在 <code>{window.__SA_LOG__}</code>
              </p>
            )}
            <p>
              <button
                className="btn"
                onClick={() => {
                  setSlow(false);
                  setRound(round + 1);
                }}
              >
                再试一次
              </button>
            </p>
          </>
        ) : (
          <p>正在启动…</p>
        )}
      </div>
    </div>
  );
}
