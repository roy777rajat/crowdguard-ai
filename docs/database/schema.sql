-- CrowdGuard AI database layout (SQLite), exported from a running install.

CREATE TABLE audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT, entity TEXT, entity_id TEXT, detail TEXT);

CREATE TABLE cameras (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, zone_id INTEGER, name TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'simulated', source_ref TEXT, active INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'registered', last_seen REAL, last_error TEXT, mode TEXT NOT NULL DEFAULT 'active');

CREATE TABLE forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, pandal_id INTEGER NOT NULL, wall_ts REAL,
  horizon_min INTEGER NOT NULL, value REAL NOT NULL);

CREATE TABLE incident_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL, ts REAL, actor TEXT, action TEXT,
  from_status TEXT, to_status TEXT, note TEXT);

CREATE TABLE incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, rule TEXT NOT NULL, severity TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', title TEXT NOT NULL, detail TEXT, dedupe_key TEXT NOT NULL,
  repeat_count INTEGER NOT NULL DEFAULT 1, assignee TEXT, run_id TEXT, created_at REAL, updated_at REAL, resolved_at REAL);

CREATE TABLE llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, run_id TEXT, provider TEXT, model TEXT, operation TEXT,
  prompt_version TEXT, started REAL, ended REAL, latency_ms REAL, input_tokens INTEGER, output_tokens INTEGER,
  status TEXT, error_category TEXT, retry_count INTEGER DEFAULT 0, est_cost_usd REAL, pricing_version TEXT,
  evaluation TEXT, prompt_sha256 TEXT, prompt_chars INTEGER);

CREATE TABLE locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE measurements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, camera_id INTEGER, sim_minute INTEGER,
  wall_ts REAL NOT NULL, occupancy REAL NOT NULL, entry_rate REAL NOT NULL, exit_rate REAL NOT NULL,
  exit_demand REAL, exit_capacity REAL, source TEXT NOT NULL DEFAULT 'simulated', run_id TEXT);

CREATE TABLE pandals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, location TEXT,
  capacity INTEGER NOT NULL, warn_pct REAL NOT NULL DEFAULT 0.7, critical_pct REAL NOT NULL DEFAULT 0.9,
  active INTEGER NOT NULL DEFAULT 1, meta TEXT, created_at REAL, updated_at REAL, location_id INTEGER);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, pandal_id INTEGER, camera_id INTEGER, source_ts REAL,
  status TEXT NOT NULL, started REAL NOT NULL, ended REAL, duration_ms REAL, retry_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT, error_message TEXT, outputs TEXT, incident_ids TEXT);

CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at REAL NOT NULL);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE stages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, name TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL, started REAL, ended REAL, duration_ms REAL, error_code TEXT, error_message TEXT);

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, salt TEXT NOT NULL,
  role TEXT NOT NULL, pandal_ids TEXT, display_name TEXT, created_at REAL);

CREATE TABLE vision_counts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, camera_id INTEGER NOT NULL, pandal_id INTEGER NOT NULL, ts REAL NOT NULL,
  count INTEGER, detector TEXT, is_real_model INTEGER NOT NULL DEFAULT 0, latency_ms REAL, status TEXT,
  error_category TEXT, run_id TEXT, zone_id INTEGER);

CREATE TABLE zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pandal_id INTEGER NOT NULL, name TEXT NOT NULL,
  exit_capacity_per_min REAL NOT NULL DEFAULT 60, active INTEGER NOT NULL DEFAULT 1, max_people INTEGER NOT NULL DEFAULT 40);

CREATE INDEX ix_cam_pandal ON cameras(pandal_id);

CREATE INDEX ix_inc_pandal ON incidents(pandal_id, status);

CREATE INDEX ix_meas_pandal ON measurements(pandal_id, id);

CREATE INDEX ix_pandal_loc ON pandals(location_id);

CREATE INDEX ix_runs_pandal ON runs(pandal_id, started);

CREATE INDEX ix_stage_run ON stages(run_id);

CREATE INDEX ix_vision_cam ON vision_counts(camera_id, id);

CREATE INDEX ix_vision_pandal_ts ON vision_counts(pandal_id, ts);

CREATE INDEX ix_zone_pandal ON zones(pandal_id);
