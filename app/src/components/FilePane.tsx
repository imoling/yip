// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 右栏里看产物：文档渲染、网页直接跑起来，对话还在左边，边看边说
import { useEffect, useState } from "react";
import { BASE, revealFile, viewUrl } from "../api";
import type { Artifact } from "../types";
import { fileBadge, fileTone } from "../util";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

export function FilePane({ a, onClose }: { a: Artifact; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const [seq, setSeq] = useState(0);
  const lower = a.name.toLowerCase();
  const pdf = lower.endsWith(".pdf");
  const img = /\.(png|jpe?g|gif|webp|bmp|svg)$/.test(lower);
  const web = lower.endsWith(".html") || lower.endsWith(".htm") || pdf;
  const office = /\.(docx|xlsx|xlsm|pptx)$/.test(lower); // 后端转成 HTML 排版好再看
  useEffect(() => {
    setText(null);
    setErr("");
    if (web || img) return;
    const token = window.__SA_TOKEN__;
    fetch(`${BASE}/api/artifacts/${a.id}/${office ? "preview" : "content"}`, { headers: token ? { authorization: `Bearer ${token}` } : {} })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.status === 415 ? "这类文件要用系统里的应用打开" : r.status === 422 ? "这个文件打不开，可能是坏的" : "文件不见了"))))
      .then(setText, (e) => setErr((e as Error).message));
  }, [a.id, a.created_at, web, img, office, seq]);
  return (
    <aside className="rail fpane">
      <div className="rh">
        <button className="icon-btn" onClick={onClose} title="回到看板和产物"><Icon name="back" /></button>
        <span className={`fi sm ${fileTone(a.name, a.kind)}`}>{fileBadge(a.name, a.kind)}</span>
        <span className="fpn" title={a.path}>{a.name}</span>
        {web || office || img ? (
          <button className="btn sm" onClick={() => setSeq(seq + 1)}>重新载入</button>
        ) : (
          <button className="btn sm" disabled={!text} onClick={async () => {
            await navigator.clipboard.writeText(text || "");
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          }}>{copied ? "已复制" : "复制全文"}</button>
        )}
        <button className="icon-btn" title={web ? "用浏览器打开" : "用系统应用打开"} onClick={() => revealFile(a.id, true)}><Icon name="external" /></button>
        <button className="icon-btn" title="在访达中显示" onClick={() => revealFile(a.id)}><Icon name="folder" /></button>
      </div>
      {img ? (
        <div className="fbody fimg"><img key={seq} src={`${viewUrl(a.id, a.name)}?t=${a.created_at}`} alt={a.name} /></div>
      ) : web ? (
        <iframe key={seq} className="fview" src={viewUrl(a.id, a.name)} title={a.name}
          sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-pointer-lock" />
      ) : office && text !== null && !err ? (
        <iframe key={seq} className="fview" srcDoc={text} title={a.name} sandbox="" />
      ) : (
        <div className="fbody sel">
          {err ? <div className="note bad">{err} <button className="btn sm" onClick={() => revealFile(a.id, true)}>用系统应用打开</button></div> : text === null ? <div className="note">正在打开…</div>
            : lower.endsWith(".md") ? <Markdown text={text} /> : <pre className="raw">{text}</pre>}
        </div>
      )}
    </aside>
  );
}
