// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
import { useEffect, useState } from "react";
import { BASE, revealFile } from "../api";
import type { Artifact } from "../types";
import { Markdown } from "./Markdown";
import { Sheet } from "./Sheet";

export function FileSheet({ a, onClose }: { a: Artifact; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const token = window.__SA_TOKEN__;
    fetch(`${BASE}/api/artifacts/${a.id}/content`, { headers: token ? { authorization: `Bearer ${token}` } : {} })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.status === 415 ? "这类文件用系统里的应用打开" : "文件不见了"))))
      .then(setText, (e) => setErr((e as Error).message));
  }, [a.id]);
  const md = a.name.toLowerCase().endsWith(".md");
  return (
    <Sheet
      title={a.name}
      wide
      onClose={onClose}
      head={
        <>
          <button className="btn sm" disabled={!text} onClick={async () => {
            await navigator.clipboard.writeText(text || "");
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          }}>{copied ? "已复制" : "复制全文"}</button>
          <button className="btn sm" onClick={() => revealFile(a.id)}>在访达中显示</button>
        </>
      }
    >
      {err ? <div className="note bad">{err}</div> : text === null ? <div className="note">正在打开…</div>
        : md ? <Markdown text={text} /> : <pre className="raw">{text}</pre>}
    </Sheet>
  );
}
