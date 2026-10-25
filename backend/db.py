"""SQLite access layer used by the repositories.

Only this module and repositories/store.py talk SQL. Everything else uses the repository
interface so that DynamoDB/S3 implementations can be added later.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

JSON_COLS = {"meta", "detail", "outputs", "incident_ids", "evaluation", "value", "pandal_ids"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS pandals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, location TEXT,
  capacity INTEGER NOT NULL, warn_pct REAL NOT NULL DEFAULT 0.7, critical_pct REAL NOT NULL DEFAULT 0.9,
  active INTEGER NOT NULL DEFAULT 1, meta TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, name TEXT NOT NULL,
  exit_capacity_per_min REAL NOT NULL DEFAULT 60, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS cameras (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, zone_id INTEGER, name TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'simulated', source_ref TEXT, active INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'registered', last_seen REAL, last_error TEXT);
CREATE TABLE IF NOT EXISTS measurements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, camera_id INTEGER, sim_minute INTEGER,
  wall_ts REAL NOT NULL, occupancy REAL NOT NULL, entry_rate REAL NOT NULL, exit_rate REAL NOT NULL,
  exit_demand REAL, exit_capacity REAL, source TEXT NOT NULL DEFAULT 'simulated', run_id TEXT);
CREATE INDEX IF NOT EXISTS ix_meas_pandal ON measurements(pandal_id, id);
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, pandal_id INTEGER NOT NULL, wall_ts REAL,
  horizon_min INTEGER NOT NULL, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, pandal_id INTEGER, camera_id INTEGER, source_ts REAL,
  status TEXT NOT NULL, started REAL NOT NULL, ended REAL, duration_ms REAL, retry_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT, error_message TEXT, outputs TEXT, incident_ids TEXT);
CREATE TABLE IF NOT EXISTS stages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, name TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL, started REAL, ended REAL, duration_ms REAL, error_code TEXT, error_message TEXT);
CREATE INDEX IF NOT EXISTS ix_stage_run ON stages(run_id);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, rule TEXT NOT NULL, severity TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', title TEXT NOT NULL, detail TEXT, dedupe_key TEXT NOT NULL,
  repeat_count INTEGER NOT NULL DEFAULT 1, assignee TEXT, run_id TEXT, created_at REAL, updated_at REAL, resolved_at REAL);
CREATE TABLE IF NOT EXISTS incident_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL, ts REAL, actor TEXT, action TEXT,
  from_status TEXT, to_status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, run_id TEXT, provider TEXT, model TEXT, operation TEXT,
  prompt_version TEXT, started REAL, ended REAL, latency_ms REAL, input_tokens INTEGER, output_tokens INTEGER,
  status TEXT, error_category TEXT, retry_count INTEGER DEFAULT 0, est_cost_usd REAL, pricing_version TEXT,
  evaluation TEXT, prompt_sha256 TEXT, prompt_chars INTEGER);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT, entity TEXT, entity_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, salt TEXT NOT NULL,
  role TEXT NOT NULL, pandal_ids TEXT, display_name TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS vision_counts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, camera_id INTEGER NOT NULL, pandal_id INTEGER NOT NULL, ts REAL NOT NULL,
  count INTEGER, detector TEXT, is_real_model INTEGER NOT NULL DEFAULT 0, latency_ms REAL, status TEXT,
  error_category TEXT);
CREATE INDEX IF NOT EXISTS ix_vision_cam ON vision_counts(camera_id, id);
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1);
"""


# Columns added after the first release. Applied idempotently so existing databases upgrade in place.
# (table, column, definition)
MIGRATIONS = [
    ("pandals", "location_id", "INTEGER"),
    ("zones", "max_people", "INTEGER NOT NULL DEFAULT 40"),
    ("cameras", "mode", "TEXT NOT NULL DEFAULT 'active'"),
    ("vision_counts", "run_id", "TEXT"),
    ("vision_counts", "zone_id", "INTEGER"),
]
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_pandal_loc ON pandals(location_id)",
    "CREATE INDEX IF NOT EXISTS ix_zone_pandal ON zones(pandal_id)",
    "CREATE INDEX IF NOT EXISTS ix_cam_pandal ON cameras(pandal_id)",
    "CREATE INDEX IF NOT EXISTS ix_vision_pandal_ts ON vision_counts(pandal_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_runs_pandal ON runs(pandal_id, started)",
    "CREATE INDEX IF NOT EXISTS ix_inc_pandal ON incidents(pandal_id, status)",
]


def _encode(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if isinstance(v, bool):
        return int(v)
    return v


def _decode(row: sqlite3.Row) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in row.keys():
        v = row[k]
        if k in JSON_COLS and isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


class Database:
    def __init__(self, path: str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            for table, column, definition in MIGRATIONS:
                cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            for stmt in INDEXES:
                self._conn.execute(stmt)
            self._conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(_encode(p) for p in params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(_encode(p) for p in params)).fetchall()
        return [_decode(r) for r in rows]

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, row: Dict[str, Any]) -> int:
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        cur = self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
        return int(cur.lastrowid or 0)

    def update(self, table: str, pk: str, pk_val: Any, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE {table} SET {sets} WHERE {pk}=?", list(fields.values()) + [pk_val])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
