// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 对话里的几种消息：你说的、Yip 回的（像人一样说话）、交付的附件、自动化按钮、记住了。
// 派活进度卡（JobCard）和插话判断（Receipt）不进对话，放在右栏「进度」里。
import { useState, type ReactNode } from "react";
import { api, rawUrl, revealFile, viewUrl } from "../api";
import type { AppState, Artifact, Decision, Intent, Job, Message, Worker } from "../types";
import { CN, INTENTS, fileBadge, isLive, isNote, pct, size, when, fileTone } from "../util";
import { avatarOf } from "./Avatar";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import icon from "../assets/icon.webp";

export interface Ctx {
  state: AppState;
  workers: Worker[];
  onOpenFile: (a: Artifact) => void;
  onError: (m: string) => void;
  onChanged: () => void;
  onMemory?: () => void;
  onAutomations?: () => void;
  onMember?: (taskId: string) => void;
  onSay?: (text: string) => void;
}

const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** 话里提到的队员换成「头像 + 名字」的名片；点一下在右栏看他在这件事里的进展。 */
export function Mentions({ text, ctx, jobId }: { text: string; ctx: Ctx; jobId?: string | null }) {
  const names = ctx.workers.map((w) => w.name).filter((n) => n && n.length >= 2).sort((a, b) => b.length - a.length);
  if (!names.length) return <>{text}</>;
  const job = ctx.state.jobs.find((j) => j.id === jobId);
  const pool = job ? job.children : ctx.state.jobs.filter((j) => isLive(j)).flatMap((j) => j.children);
  const parts = text.split(new RegExp(`(${names.map(esc).join("|")})`, "g"));
  return (
    <>
      {parts.map((part, i) => {
        const w = i % 2 ? ctx.workers.find((x) => x.name === part) : undefined;
        if (!w) return part;
        const mine = pool.filter((k) => k.worker_id === w.id);
        const kid = mine.find((k) => isLive(k)) || mine[mine.length - 1];
        return (
          <button key={i} className={`who ${kid ? "" : "off"}`} disabled={!kid || !ctx.onMember}
            onClick={() => kid && ctx.onMember?.(kid.id)} title={kid ? `${kid.title}：点一下看进展` : w.name}>
            <img src={avatarOf(w.id, ctx.workers)} alt="" />{w.name}
          </button>
        );
      })}
    </>
  );
}

export function Say({ children, think }: { children: ReactNode; think?: boolean }) {
  return (
    <div className={`say ${think ? "think" : ""}`}>
      <img src={icon} alt="" />
      <div className={`bub sel ${think ? "think" : ""}`}>{children}</div>
    </div>
  );
}

export function JobCard({ job, ctx }: { job: Job; ctx: Ctx }) {
  const [sure, setSure] = useState<string | null>(null); // 点了一次「停下」的是哪件 / 哪个队员，再点一次才停
  async function stop(id: string) {
    if (sure !== id) {
      setSure(id);
      setTimeout(() => setSure((x) => (x === id ? null : x)), 4000);
      return;
    }
    setSure(null);
    try {
      await api(`/api/tasks/${id}/cancel`, { body: {} });
      ctx.onChanged();
    } catch (e) {
      ctx.onError(`没停下：${(e as Error).message}`);
    }
  }
  const asks = ctx.state.approvals.filter((a) => a.main_id === job.id);
  const waiting = asks.length > 0 || ctx.state.decisions.some((d) => d.task_id === job.id && d.status === "pending_confirm");
  async function answer(id: number, decision: "allow" | "deny") {
    try {
      await api(`/api/approvals/${id}`, { body: { decision } });
      ctx.onChanged();
    } catch (e) {
      ctx.onError(`没回成：${(e as Error).message}`);
    }
  }
  const tag = !isLive(job)
    ? job.status === "done" ? (job.verdict && !job.verdict.pass ? ["w", `还差 ${job.verdict.problems.length} 处`] : ["", "已完成"]) : ["bad", job.status === "failed" ? "中断了" : "已取消"]
    : waiting ? ["w", "等你确认"] : ["", `进行中 ${pct(job.progress)}`];
  return (
    <div className={`job ${waiting && isLive(job) ? "ask" : ""}`}>
      <div className="h">
        <span>{job.title}</span>
        <span className={`tg ${tag[0]}`}>{tag[1]}</span>
        <span className="sp" />
        {isLive(job) && (
          <button className={`lk stop ${sure === job.id ? "sure" : ""}`} onClick={() => stop(job.id)}>
            {sure === job.id ? "确定停下？" : "停下"}
          </button>
        )}
      </div>
      {job.children.length > 0 && (
        <div className="jk">
          {job.children.map((k) => {
            const bad = k.verdict && !k.verdict.pass;
            const st = k.status === "done" && k.note?.startsWith("等 ") ? ["off", k.note]
              : k.status === "done" ? (bad ? ["bad", k.note || "有问题没改完"] : ["ok", k.verdict?.reworks ? `验收通过，返工 ${k.verdict.reworks} 次` : "已交"]) : k.status === "failed" ? ["bad", k.note || "中断了"]
              : k.status === "cancelled" ? ["off", "已取消"] : k.status === "waiting" ? ["off", k.note || "排队中"]
              : ["", k.note || "开工中"];
            return (
              <div key={k.id} className={`r ${st[0]} ${ctx.onMember ? "click" : ""}`} title={bad ? k.verdict!.problems.join("\n") : "点一下看他的进展"}
                onClick={() => ctx.onMember?.(k.id)}>
                <img src={avatarOf(k.worker_id, ctx.workers)} alt="" />
                <span className="t">{k.title}</span>
                <span className="n">
                  {k.status === "running" && <span className="kbar"><i style={{ width: `${Math.max(8, Math.round(k.progress * 100))}%` }} /></span>}
                  {st[1]}
                </span>
                {isLive(k) && (
                  <button className={`kstop ${sure === k.id ? "sure" : ""}`} onClick={(e) => { e.stopPropagation(); stop(k.id); }}
                    title={`只停下 ${k.label}，其他人继续`} aria-label={`停下 ${k.label}`}>
                    {sure === k.id ? "确定？" : <Icon name="x" />}
                  </button>
                )}
              </div>
            );
          })}
          {isLive(job) && job.children.every((k) => !isLive(k)) && <div className="r sum">正在汇总…</div>}
          {asks.map((a) => {
            const who = ctx.workers.find((w) => w.id === a.worker_id)?.name || "队员";
            return (
              <div key={a.id} className="jask">
                <span>{who}想{a.tool === "mail_send" ? "发邮件" : "动手"}</span>
                <code title={a.summary}>{a.summary}</code>
                <button className="btn sm" onClick={() => answer(a.id, "deny")}>拒绝</button>
                <button className="btn sm p" onClick={() => answer(a.id, "allow")}>允许</button>
              </div>
            );
          })}
          {job.team?.map((t) => (
            <div key={t.id} className={`tm ${t.kind}`} title={t.text}>
              <Icon name={t.kind === "issue" ? "restart" : "chat"} />
              <b>{t.from} → {t.to || "全队"}</b>
              <span>{t.kind === "issue" ? "退回：" : ""}{t.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const ICON: Record<Intent, string> = { augment: "merge", append: "queue", overturn: "restart" };

export function Receipt({ dec, job, unsure, said, stale, ctx }: { dec: Decision; job?: Job; unsure?: boolean; said?: string; stale?: boolean; ctx: Ctx }) {
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const intent = (dec.final_action || dec.intent) as Intent;
  const kid = job?.children.find((k) => k.id === dec.target);
  const who = kid ? `「${kid.title}」` : "整件事";
  const pending = dec.status === "pending_confirm";
  const head = intent === "augment" ? `融进 ${who}` : intent === "append" ? (kid ? `等 ${who} 做完接上` : "排在最后做")
    : pending ? `${who} 要重来，等你确认` : `${who} 推翻重来`;
  async function override(i: Intent) {
    setBusy(true);
    try {
      await api(`/api/decisions/${dec.id}/override`, { body: { intent: i } });
      setEdit(false);
      ctx.onChanged();
    } catch (e) {
      ctx.onError(`没改成：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={`rc ${intent === "overturn" ? "w" : ""}`} title={dec.reason}>
      <Icon name={ICON[intent]} />
      <span className="t">{said && !pending ? said : <><em>{CN[intent]}</em>，{head}</>}</span>
      {(unsure || dec.confidence < 0.6) && <span className="unsure">没把握</span>}
      {stale || !job || !isLive(job) ? null : edit ? (
        <div className="seg" role="radiogroup" aria-label="改判断">
          {INTENTS.map((i) => (
            <button key={i} role="radio" aria-checked={i === intent} className={i === intent ? "on" : ""}
              disabled={busy || dec.status === "running"} onClick={() => (i === intent ? setEdit(false) : override(i))}>
              {CN[i]}
            </button>
          ))}
        </div>
      ) : (
        <button className="chg" onClick={() => setEdit(true)}>改成…</button>
      )}
    </div>
  );
}

function Delivery({ m, ctx }: { m: Message; ctx: Ctx }) {
  const meta = m.meta || {};
  const find = (id?: number) => ctx.state.artifacts.find((x) => x.id === id);
  const sum = find(meta.artifact_id);
  const all = [...(meta.files || []).map(find), sum].filter(Boolean) as Artifact[];
  // 同名同大小的只留一份；交了能直接用的东西时，队员自己的笔记不列
  const uniq = all.filter((f, i) => all.findIndex((x) => x.name === f.name && x.size === f.size) === i);
  const usable = uniq.some((f) => !/\.(md|txt)$/i.test(f.name));
  const hasDocFile = uniq.some((f) => /\.(docx|pdf)$/i.test(f.name));
  const rows = uniq.filter((f) => {
    if (hasDocFile && /\.(md|txt)$/i.test(f.name)) return false;
    if (usable && /\.(md|txt)$/.test(f.name) && isNote(f.name, ctx.workers)) return false;
    return true;
  });
  const kids = ctx.state.jobs.find((j) => j.id === m.task_id)?.children || [];
  const dup = (n: string) => rows.filter((x) => x.name === n).length > 1;
  const web = rows.find((f) => /\.html?$/i.test(f.name));
  const pics = rows.filter((f) => /\.(png|jpe?g|gif|webp)$/i.test(f.name));  // 图片直接显示，点一下在右栏看大图
  return (
    <>
      <Say><Mentions text={m.content} ctx={ctx} jobId={m.task_id} /></Say>
      {rows.length > 0 && (
        <div className="deliv">
          {web && (
            // 网页直接给一张能看的缩略图，点一下在右栏打开玩
            <button className="webpv" onClick={() => ctx.onOpenFile(web)} title={web.path}>
              <span className="shot"><iframe src={viewUrl(web.id, web.name)} title={web.name} tabIndex={-1} sandbox="allow-scripts allow-same-origin" /></span>
              <span className="bar"><b>{web.path.split("/").slice(-2, -1)[0] || web.name}</b><small>{web.name} · 点一下在右边打开</small></span>
            </button>
          )}
          {pics.length > 0 && (
            <div className="dpics">
              {pics.map((f) => (
                <button key={f.id} onClick={() => ctx.onOpenFile(f)} title={f.name}>
                  <img src={viewUrl(f.id, f.name)} alt={f.name} />
                </button>
              ))}
            </div>
          )}
          {rows.filter((f) => f !== web && !pics.includes(f)).map((f) => {
            const kid = kids.find((k) => k.id === f.task_id);
            return (
              <div className="dr" key={f.id}>
                <button className="dopen" onClick={() => ctx.onOpenFile(f)} title={f.path}>
                  <span className={`fi sm ${fileTone(f.name, f.kind)}`}>{fileBadge(f.name, f.kind)}</span>
                  <span className="dn">
                    <b>{dup(f.name) ? f.path.split("/").slice(-2).join("/") : f.name}</b>
                    <small>{[f === sum ? "汇总文档" : kid ? kid.title : "", size(f.size)].filter(Boolean).join(" · ")}</small>
                  </span>
                </button>
                <button className="icon-btn" title="在访达中显示" onClick={() => revealFile(f.id).catch((e) => ctx.onError((e as Error).message))}>
                  <Icon name="folder" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

export function FileCard({ a, ctx, final }: { a: Artifact; ctx: Ctx; final?: boolean }) {
  const job = ctx.state.jobs.find((j) => j.id === a.main_id);
  const kid = job?.children.find((k) => k.id === a.task_id);
  return (
    <div className="file">
      <div className="fh">
        <span className={`fi ${fileTone(a.name, a.kind)}`}>{fileBadge(a.name, a.kind)}</span>
        <div style={{ minWidth: 0 }}>
          <div className="fn">{a.name}</div>
          <div className="fm">
            {[final ? "汇总文档" : kid ? kid.title : job?.title, size(a.size), when(a.created_at)]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>
      </div>
      {a.preview && <div className="pv sel">{a.preview}</div>}
      <div className="fa">
        <button className="btn p sm" onClick={() => ctx.onOpenFile(a)}>
          打开
        </button>
        <button className="btn sm" onClick={() => revealFile(a.id).catch((e) => ctx.onError((e as Error).message))}>
          在访达中显示
        </button>
      </div>
    </div>
  );
}

function AutoSay({ m, ctx, stale }: { m: Message; ctx: Ctx; stale?: boolean }) {
  const [ran, setRan] = useState(false);
  async function run() {
    try {
      await api(`/api/automations/${m.meta!.automation_id}/run`, { body: {} });
      setRan(true);
      ctx.onChanged();
    } catch (e) {
      ctx.onError(`没跑起来：${(e as Error).message}`);
    }
  }
  return (
    <>
      <Say>{m.content}</Say>
      {m.meta?.automation_id && !stale ? (
        <div className="fchips">
          <button className="fchip" disabled={ran} onClick={run}><Icon name="play" />{ran ? "已开始跑" : "现在先跑一次"}</button>
          <button className="fchip" onClick={ctx.onAutomations}><Icon name="clock" />去改时间</button>
        </div>
      ) : null}
    </>
  );
}

export function MessageRow({ m, ctx, stale }: { m: Message; ctx: Ctx; stale?: boolean }) {
  const meta = m.meta || {};
  if (m.role === "user") {
    const atts = (meta as { attachments?: { id: number; name: string; kind: string; size: number }[] }).attachments || [];
    const job = m.task_id ? ctx.state.jobs.find((j) => j.id === m.task_id) : undefined;
    const pinnedKid = job?.worker_id ? job.children.find((k) => k.worker_id === job.worker_id) || job.children[0] : undefined;
    const hasAt = m.content.startsWith("@") && pinnedKid && ctx.onMember;
    return (
      <>
        <div className="you">
          <span className="sel">
            {hasAt ? <Mentions text={m.content} ctx={ctx} jobId={m.task_id} /> : m.content}
          </span>
        </div>
        {atts.length > 0 && (
          <div className="uatts">
            {atts.map((a) => a.kind === "image" ? (
              <img key={a.id} src={rawUrl(`/api/uploads/${a.id}/raw`)} alt={a.name} title={a.name} />
            ) : (
              <span key={a.id} className="uf" title={a.name}><span className="fi sm other">{a.name.split(".").pop()?.toUpperCase().slice(0, 4)}</span>{a.name} · {size(a.size)}</span>
            ))}
          </div>
        )}
      </>
    );
  }
  if (m.role === "decision") return null; // 插话怎么理解的、要改成别的：在右栏「进度」里
  if (meta.kind === "plan")
    return <Say><Mentions text={/^(好|行|嗯|收到)/.test(m.content) ? m.content : `好，${m.content.replace(/^已拆成/, "拆成")}`} ctx={ctx} jobId={m.task_id} /></Say>;
  if (meta.kind === "file" && meta.final) return <Delivery m={m} ctx={ctx} />;
  if (meta.kind === "file") {
    const a = ctx.state.artifacts.find((x) => x.id === meta.artifact_id);
    return (
      <>
        <Say>{m.content}</Say>
        {a && <FileCard a={a} ctx={ctx} final={meta.final} />}
        {meta.files?.length ? (
          <div className="fchips">
            {(() => {
              const fs = meta.files.map((id) => ctx.state.artifacts.find((x) => x.id === id)).filter(Boolean) as Artifact[];
              // 同名文件（两个 index.html）带上所在文件夹
              const dup = (n: string) => fs.filter((x) => x.name === n).length > 1;
              return fs.map((f) => (
                <button key={f.id} className="fchip" onClick={() => ctx.onOpenFile(f)} title={f.path}>
                  <span className={`fi sm ${fileTone(f.name, f.kind)}`}>{fileBadge(f.name, f.kind)}</span>
                  {dup(f.name) ? f.path.split("/").slice(-2).join("/") : f.name}
                </button>
              ));
            })()}
          </div>
        ) : null}
      </>
    );
  }
  if (meta.kind === "artifact") return <Say>{m.content}</Say>;
  if (meta.kind === "automation") return <AutoSay m={m} ctx={ctx} stale={stale} />;
  if (meta.kind === "memory")
    return (
      <button className="aside" onClick={ctx.onMemory} title="去记忆里看">
        <Icon name="bookmark" />
        {m.content}
      </button>
    );
  if (meta.kind === "chat") {
    // 大纲 / 计划等你点头：下面放一个「开工」，想改直接在输入框里说
    const waiting = (meta as { plan_ask?: boolean }).plan_ask && !stale
      && ctx.state.jobs.some((j) => j.id === m.task_id && j.note === "等你点头")
      && !ctx.state.messages.some((x) => x.id > m.id && x.task_id === m.task_id && (x.meta as { plan_ask?: boolean } | null)?.plan_ask);
    return (
      <>
        <Say>
          <Markdown text={m.content} />
        </Say>
        {waiting && ctx.onSay && (
          <div className="fchips">
            <button className="fchip primary" onClick={() => ctx.onSay!("开工")}><Icon name="play" />开工</button>
          </div>
        )}
      </>
    );
  }
  return <Say><Mentions text={m.content} ctx={ctx} jobId={m.task_id} /></Say>;
}

export type MsgCtx = Ctx;
