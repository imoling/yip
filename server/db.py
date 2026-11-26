"""SQLite 数据层。所有组件只通过这里和数据库交互，不共享内存状态。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SA_DATA_DIR", ROOT / "data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, parent_id TEXT, label TEXT, title TEXT NOT NULL, goal TEXT,
  constraints TEXT DEFAULT '[]', status TEXT NOT NULL, progress REAL DEFAULT 0,
  steps INTEGER DEFAULT 4, note TEXT DEFAULT '',
  thread_id TEXT, run_id TEXT, depends_on TEXT, worker_id TEXT, assign_reason TEXT,
  result TEXT, artifact TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT, utterance TEXT NOT NULL, intent TEXT NOT NULL, target TEXT,
  reason TEXT, cost TEXT, confidence REAL, detail TEXT, source TEXT,
  need_confirm INTEGER DEFAULT 0, progress REAL DEFAULT 0, status TEXT DEFAULT 'done',
  final_action TEXT, overridden INTEGER DEFAULT 0, undo TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT, payload TEXT, seq INTEGER, consumed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL, main_id TEXT, run_id TEXT, worker_id TEXT, tool TEXT NOT NULL,
  args TEXT, summary TEXT, risk TEXT, status TEXT DEFAULT 'pending', created_at INTEGER NOT NULL, decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT, main_id TEXT, worker_id TEXT, path TEXT NOT NULL, name TEXT NOT NULL,
  kind TEXT, size INTEGER DEFAULT 0, preview TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL, worker_id TEXT, kind TEXT NOT NULL, content TEXT NOT NULL,
  confirmed INTEGER DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  rule TEXT, progress_note TEXT DEFAULT '', started_at INTEGER NOT NULL, last_review_at INTEGER,
  next_review_at INTEGER, last_job TEXT, updated_at INTEGER NOT NULL
);
"""

TERMINAL = ("done", "failed", "cancelled")
JSON_COLS = {"constraints", "payload", "meta", "undo", "args", "grants", "prereqs", "accept", "verdict", "rule"}
# 老库补列：(表, 列, 定义)
COLUMNS = [("tasks", "grants", "TEXT DEFAULT '[]'"), ("memories", "source", "TEXT DEFAULT 'job'"),
           ("memories", "updated_at", "INTEGER"), ("memories", "used_at", "INTEGER"),
           ("tasks", "kind", "TEXT DEFAULT ''"), ("tasks", "prereqs", "TEXT DEFAULT '[]'"),
           ("tasks", "accept", "TEXT DEFAULT '[]'"), ("tasks", "verdict", "TEXT"),
           ("tasks", "return_to", "TEXT"), ("tasks", "bounces", "INTEGER DEFAULT 0"),
           ("tasks", "plan_first", "INTEGER DEFAULT 0"), ("tasks", "plan_ok", "INTEGER DEFAULT 0"),
           ("tasks", "hard", "INTEGER DEFAULT 0"), ("tasks", "outline", "TEXT"),
           ("tasks", "reviewed", "INTEGER DEFAULT 0"), ("tasks", "redeliver", "INTEGER DEFAULT 0"),
           ("tasks", "goal_id", "TEXT"), ("memories", "goal_id", "TEXT"),
           ("approvals", "answer", "TEXT")]

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str = "t") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def connect(path: Path | None = None) -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(path or DATA_DIR / "app.db"), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            for table, col, ddl in COLUMNS:
                have = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
                if col not in have:
                    _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            _conn.commit()
        return _conn


def reset_for_tests(path: Path) -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        connect(path)


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [_row(r) for r in connect().execute(sql, args).fetchall()]


def q1(sql: str, args: tuple = ()) -> dict | None:
    with _lock:
        return _row(connect().execute(sql, args).fetchone())


def ex(sql: str, args: tuple = ()) -> int:
    with _lock:
        c = connect()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def _enc(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v


def insert(table: str, **cols) -> int:
    keys = list(cols)
    return ex(
        f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
        tuple(_enc(cols[k]) for k in keys),
    )


def update(table: str, key, **cols) -> None:
    if table == "tasks":
        cols.setdefault("updated_at", now())
    sets = ",".join(f"{k}=?" for k in cols)
    ex(f"UPDATE {table} SET {sets} WHERE id=?", tuple(_enc(v) for v in cols.values()) + (key,))


# ---------- tasks ----------
def task(tid: str) -> dict | None:
    return q1("SELECT * FROM tasks WHERE id=?", (tid,))


def children(parent_id: str) -> list[dict]:
    return q("SELECT * FROM tasks WHERE parent_id=? ORDER BY created_at, label", (parent_id,))


def main_tasks(trials: bool = False) -> list[dict]:
    """trials=False 时不含「试一下」建的试用活：它们不进对话、不进 Yip 的上下文。"""
    if trials:
        return q("SELECT * FROM tasks WHERE parent_id IS NULL ORDER BY created_at DESC")
    return q("SELECT * FROM tasks WHERE parent_id IS NULL AND COALESCE(kind,'')!='trial' ORDER BY created_at DESC")


def next_label(parent_id: str) -> str:
    n = len(children(parent_id))
    return chr(ord("A") + n) if n < 26 else f"#{n + 1}"


def next_seq() -> int:
    r = q1("SELECT COALESCE(MAX(seq), 0) + 1 AS s FROM task_queue")
    return r["s"]


def recent_events(tid: str, n: int = 3) -> list[str]:
    rows = q("SELECT payload FROM task_events WHERE task_id=? AND type='step' ORDER BY id DESC LIMIT ?", (tid, n))
    return [r["payload"].get("text", "") if isinstance(r["payload"], dict) else str(r["payload"]) for r in rows]
