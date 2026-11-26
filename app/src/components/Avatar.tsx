// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 队员头像：16 张预置（素材在 assets/brand/workers/）。队员设过头像就用设的，内置队员有默认，其余按 id 固定挑一张。
import type { Worker } from "../types";

const files = import.meta.glob("../assets/workers/*.{webp,svg}", { eager: true, import: "default" }) as Record<string, string>;
export const AVATARS: Record<string, string> = Object.fromEntries(
  Object.entries(files).map(([p, url]) => [p.split("/").pop()!.replace(/\.(webp|svg)$/, ""), url]),
);
export const AVATAR_IDS = [
  "intel-researcher", "data-analyst", "report-writer", "general-assistant", "mail-assistant", "support", "inspector",
  "accountant", "marketer", "designer", "business", "teacher", "coder", "generic-1", "generic-2", "generic-3", "generic-4",
  "extra-1", "extra-2", "extra-3", "extra-4", "extra-5", "extra-6", "extra-7", "extra-8", "extra-9", "extra-10",
].filter((k) => AVATARS[k]);
// 内置队员的默认头像（没有专属图的借用预置里最贴的一张）
const DEFAULT: Record<string, string> = {
  "requirements-analyst": "business", developer: "coder", tester: "inspector", "code-reviewer": "teacher",
  "meeting-secretary": "support", "document-clerk": "accountant", "report-assistant": "marketer", "ppt-designer": "designer",
  "file-keeper": "generic-1",
};
const SPARE = AVATAR_IDS.filter((k) => !["intel-researcher", "data-analyst", "report-writer", "general-assistant", "mail-assistant"].includes(k));

export function avatarOf(id: string | null | undefined, workers?: Worker[]): string {
  const w = workers?.find((x) => x.id === id);
  if (w?.avatar && AVATARS[w.avatar]) return AVATARS[w.avatar];
  if (!id) return AVATARS["general-assistant"];
  if (AVATARS[id]) return AVATARS[id];
  if (DEFAULT[id] && AVATARS[DEFAULT[id]]) return AVATARS[DEFAULT[id]];
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return AVATARS[SPARE[h % SPARE.length]];
}

export function Avatar({ id, workers, size = 30 }: { id: string | null | undefined; workers?: Worker[]; size?: number }) {
  return <img src={avatarOf(id, workers)} alt="" style={{ width: size, height: size, borderRadius: "50%" }} draggable={false} />;
}
