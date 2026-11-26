// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 后端 /api 返回的形状，字段名和 server/db.py 的表一致。界面上「队员」= 代码里的 worker。

export type Intent = "augment" | "append" | "overturn";
export type TaskStatus = "pending" | "waiting" | "running" | "done" | "failed" | "cancelled";

export interface Task {
  id: string;
  parent_id: string | null;
  label: string;
  title: string;
  goal: string;
  constraints: string[] | null;
  status: TaskStatus;
  progress: number;
  note: string | null;
  worker_id: string | null;
  assign_reason: string | null;
  artifact: string | null;
  result: string | null;
  depends_on: string | null;
  goal_id?: string | null;
  verdict?: { pass: boolean; problems: string[]; reworks?: number; score?: number | null } | null;
  created_at: number;
  accept?: string[];
  updated_at: number;
}

export interface TeamMsg {
  id: number;
  from_task: string | null;
  to_task: string | null;
  from: string;
  to: string | null;
  kind: "note" | "issue";
  text: string;
  created_at: number;
}

export interface Job extends Task {
  children: Task[];
  team?: TeamMsg[];
}

export interface Decision {
  id: number;
  task_id: string;
  utterance: string;
  intent: Intent;
  target: string | null;
  reason: string;
  confidence: number;
  detail: string | null;
  source: "model" | "rule" | "fallback";
  need_confirm: number;
  progress: number;
  status: "pending_confirm" | "running" | "done" | "failed";
  final_action: Intent | null;
  overridden: number;
}

export interface PlanItem {
  label: string;
  title: string;
  worker: string;
  reason: string;
}

export interface Message {
  id: number;
  task_id: string | null;
  role: "user" | "assistant" | "decision";
  content: string;
  meta: {
    kind?: "plan" | "artifact" | "file" | "chat" | "memory" | "automation" | "clear";
    automation_id?: number;
    items?: PlanItem[];
    decision_id?: number;
    unsure?: boolean;
    artifact_id?: number;
    final?: boolean;
    files?: number[];
    act?: number; // 这条是哪个判断执行后的回话；改判断后只留最新一条
  } | null;
  created_at: number;
}

export interface Approval {
  id: number;
  task_id: string;
  main_id: string;
  worker_id: string | null;
  tool: string;
  summary: string;
  risk: "read" | "write" | "exec" | "external";
  status: string;
  created_at: number;
  args?: Record<string, unknown>;
}

export interface Artifact {
  id: number;
  task_id: string;
  main_id: string;
  worker_id: string | null;
  path: string;
  name: string;
  kind: "doc" | "sheet" | "slides" | "other";
  size: number;
  preview: string;
  created_at: number;
}

export interface Goal {
  id: string;
  title: string;
  target: string;
  status: "active" | "paused" | "done";
  rule: Record<string, unknown> | null;
  when: string;
  progress_note: string;
  days: number;
  started_at: number;
  last_review_at: number | null;
  next_review_at: number | null;
  last_job: string | null;
  job_count: number;
  jobs: { id: string; title: string; status: string; updated_at: number; note: string }[];
  artifacts: { id: number; name: string; kind: string; path: string; created_at: number }[];
  memories: { id: number; kind: string; content: string; created_at: number }[];
}

export interface StarterPack {
  id: string;
  title: string;
  tag: string;
  prompt: string;
  mode: "once" | "schedule";
  rule?: Record<string, unknown>;
  when?: string;
}

export interface AppState {
  jobs: Job[];
  messages: Message[];
  decisions: Decision[];
  approvals: Approval[];
  artifacts: Artifact[];
  workspace: string;
  connectors: number;
  name?: string;
  avatar?: string;
  goals?: Goal[];
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  has_script?: boolean;
}

export interface Worker {
  id: string;
  name: string;
  introduction: string;
  personality: string[];
  skills: Skill[];
  inputs: string;
  deliverable: string;
  source: "builtin" | "custom" | "imported" | "external";
  enabled: boolean;
  kind: string;
  avatar: string;
  team?: string;
  global_skills: string[];
  connectors: string[] | null;
}

export interface ExternalAgent {
  id: string;
  name: string;
  protocol: "acp" | "cli" | "openapi";
  status: "ready" | "planned";
  introduction: string;
  avatar: string;
  binary: string;
  env_key: string;
  default_auth: "cli" | "api_key";
  safety_note: string;
  install_hint: string;
  install_cmd: string;
  enabled: boolean;
  auth_mode: "cli" | "api_key";
  has_key: boolean;
  api_key: string;
  command: string;
  installed: boolean;
  path: string;
  probe_reason: string;
  checked_ok: boolean;
  check_msg: string;
  checked_at: number;
}

export interface Preset {
  id: string;
  name: string;
  base_url: string;
  model: string;
  fast_model: string;
  key_url: string;
  needs_key: boolean;
  note: string;
}

export interface YipSettings {
  approval: "auto" | "plan" | "ask_writes" | "allow_all" | "ask_all";
  overturn_confirm: number;
  classify_timeout: number;
  low_confidence: number;
  max_parallel: number;
  max_rounds: number;
  workspace: string;
  search: { provider: "keenable" | "ddgs" | "tavily" | "bocha"; api_key: string; deep_provider?: "" | "tavily" | "bocha"; deep_key?: string };
  note: string;
  engine?: "classic" | "deep";
  soul?: Soul;
}

export interface Soul {
  name: string;
  call_me: string;
  tone: "steady" | "warm" | "cheeky";
  relation: "assistant" | "partner" | "friend";
  emoji: "none" | "few";
  intro: string;
  proactive: boolean;
}

export interface Settings {
  providers: Record<string, { base_url: string; api_key: string; has_key: boolean }>;
  model: string;
  fast_model: string;
  vision_model?: string;
  mail?: MailConfig;
  presets: Preset[];
  yip: YipSettings;
  configured: boolean;
  provider: string;
  model_name: string;
  status: { state: "ok" | "error" | "off" | string; text: string; detail?: string };
}

export interface Report {
  ok: boolean;
  errors: string[];
  warns: string[];
}

export interface WorkerForm {
  id?: string;
  name?: string;
  introduction?: string;
  personality?: string[];
  capability?: string;
  skills?: { id?: string; name?: string; description?: string; not_for?: string }[];
  inputs?: string;
  deliverable?: string;
  identity?: string;
  avatar?: string;
  global_skills?: string[] | null;
  missing?: string[];
  sample?: string;
}

export interface GlobalSkill {
  id: string;
  name: string;
  description: string;
  source: string;
  owner?: string;
  has_script: boolean;
  dir: string;
  users?: string[];
}

export interface Connector {
  name: string;
  config: Record<string, unknown>;
  transport: "stdio" | "http";
  enabled: boolean;
  requires_approval: boolean;
  connected: boolean;
  tools: number;
  error: string;
}

export interface MemoryItem {
  id: number;
  kind: "persona" | "preference" | "fact" | "conclusion" | "lesson" | "state";
  content: string;
  source: string;
  task_id: string;
  confirmed: number;
  created_at: number;
  updated_at: number | null;
  used_at: number | null;
}

export interface McpPreset {
  id: string;
  name: string;
  desc: string;
  needs: string;
  fields: { key: string; label: string; default?: string; secret?: boolean; url?: string }[];
  ready: boolean;
  added: boolean;
}

export interface TaskEvent {
  id: number;
  task_id: string;
  type: string;
  payload: { text?: string } | null;
  created_at: number;
}

export interface MailConfig {
  provider?: string;
  address?: string;
  name?: string;
  password?: string;
  imap_host?: string;
  imap_port?: number;
  smtp_host?: string;
  smtp_port?: number;
}
