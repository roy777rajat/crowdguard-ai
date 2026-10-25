export type Role = 'admin' | 'operator' | 'manager' | 'auditor';
export type Risk = 'normal' | 'warning' | 'critical';

export interface User {
  id: number;
  username: string;
  role: Role;
  display_name: string | null;
  pandal_ids: number[];
}

export interface Quality {
  confidence: number;
  label: string;
  reasons: string[];
}

export interface Bottleneck {
  flag: boolean;
  pressure: number | null;
  reason: string;
}

export interface PandalState {
  pandal_id: number;
  capacity: number;
  warn_threshold: number;
  critical_threshold: number;
  source: string;
  has_data: boolean;
  stale: boolean;
  status: string;
  age_s: number | null;
  quality: Quality;
  occupancy?: number;
  occupancy_pct?: number;
  entry_rate?: number;
  exit_rate?: number;
  forecast?: Record<string, number>;
  risk?: Risk;
  minutes_to_warning?: number | null;
  minutes_to_critical?: number | null;
  bottleneck?: Bottleneck | null;
  last_measurement_wall_ts?: number;
  sim_minute?: number | null;
}

export interface Zone {
  id: number;
  pandal_id: number;
  name: string;
  exit_capacity_per_min: number;
  active: number;
  max_people?: number;
}

export interface Location {
  id: number;
  name: string;
  active: number;
  pandal_count: number;
}

export interface Camera {
  id: number;
  pandal_id: number;
  zone_id: number | null;
  name: string;
  source_type: 'simulated' | 'local_video' | 'live_stream';
  source_ref: string | null;
  mode?: 'active' | 'passive';
  active: number;
  status: string;
  last_seen: number | null;
  last_error: string | null;
  connection_note?: string;
}

export interface Pandal {
  id: number;
  location_id?: number | null;
  name: string;
  location: string | null;
  capacity: number;
  warn_pct: number;
  critical_pct: number;
  active: number;
  zones?: Zone[];
  cameras?: Camera[];
}

export interface Run {
  run_id: string;
  trace_id: string;
  pandal_id: number | null;
  camera_id: number | null;
  source_ts: number | null;
  status: string;
  started: number;
  ended: number | null;
  duration_ms: number | null;
  retry_count: number;
  error_code: string | null;
  error_message: string | null;
  outputs: Record<string, unknown> | null;
  incident_ids: number[] | null;
}

export interface Stage {
  id: number;
  run_id: string;
  name: string;
  attempt: number;
  status: string;
  started: number | null;
  ended: number | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface Incident {
  id: number;
  pandal_id: number;
  rule: string;
  severity: 'info' | 'warning' | 'critical';
  status: 'open' | 'acknowledged' | 'assigned' | 'resolved';
  title: string;
  detail: Record<string, unknown> | null;
  repeat_count: number;
  assignee: string | null;
  run_id: string | null;
  created_at: number;
  updated_at: number;
  resolved_at: number | null;
}

export interface IncidentEvent {
  id: number;
  incident_id: number;
  ts: number;
  actor: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  note: string | null;
}

export interface LlmCall {
  id: number;
  trace_id: string | null;
  run_id: string | null;
  provider: string;
  model: string;
  operation: string;
  prompt_version: string;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  status: string;
  error_category: string | null;
  retry_count: number;
  est_cost_usd: number | null;
  pricing_version: string | null;
  evaluation: { grounded: boolean; ungrounded_numbers: string[]; check: string } | null;
  started: number;
}

export interface LlmGroup {
  requests: number;
  success: number;
  errors: number;
  error_rate: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  calls_missing_tokens: number;
  est_cost_usd: number | null;
  calls_missing_cost: number;
}

export interface LlmSummary extends LlmGroup {
  by_model: Record<string, LlmGroup>;
  by_operation: Record<string, LlmGroup>;
  budget: { limit_usd: number | null; spent_usd: number | null; status: string; note: string };
  pricing_version: string;
}

export interface Simulation {
  running: boolean;
  scenario: string;
  speed: number;
  minute: number;
  scenarios: string[];
  source: string;
}

export interface DashCard {
  pandal: Pandal;
  state: PandalState;
  cameras: Camera[];
  zones: Zone[];
  active_incidents: number;
  critical_incidents: number;
}

export interface Dashboard {
  simulation: Simulation;
  pandals: DashCard[];
  recent_runs: Run[];
  llm: LlmSummary | null;
  server_time: number;
  data_notice: string;
}

export interface SeriesPoint {
  minute: number | null;
  occupancy: number;
  entry_rate: number;
  exit_rate: number;
  wall_ts: number;
}

export interface Series {
  pandal_id: number;
  source: string;
  points: SeriesPoint[];
  warn_threshold: number;
  critical_threshold: number;
  state: PandalState;
}

export interface AuditRow {
  id: number;
  ts: number;
  actor: string;
  action: string;
  entity: string;
  entity_id: string | null;
  detail: Record<string, unknown> | null;
}

export interface Settings {
  max_data_age_s: number;
  sim_speed: number;
  llm_budget_usd: number | null;
  predicted_critical_lead_min: number;
  rules_enabled: Record<string, boolean>;
  data_source: string;
  model_integration: {
    active_provider: string;
    model: string;
    is_real_model: boolean;
    meta_adapter: string;
    credential_set: boolean;
    note: string;
    credentials_required: boolean;
  };
  auth_mode: string;
  notifications: string;
}

export type CrowdLevel = 'quiet' | 'busy' | 'crowded' | 'unknown' | 'passive';

export interface WallZone {
  zone: Zone;
  camera: Camera | null;
  limits: { max_people: number; warn: number; critical: number };
  count: number | null;
  stale: boolean;
  age_s: number | null;
  pct_of_threshold: number | null;
  pct_criticality: number | null;
  level: CrowdLevel;
  series: Array<{ ts: number; count: number }>;
  last_error: string | null;
}

export interface WallTotals {
  count: number | null;
  warn: number;
  critical: number;
  pct_of_threshold: number | null;
  pct_criticality: number | null;
  level: CrowdLevel;
  cameras_active: number;
  cameras_reporting: number;
}

export interface WallView {
  pandal: Pandal;
  zones: WallZone[];
  totals: WallTotals;
  sum_series: Array<{ ts: number; count: number }>;
  window_s: number;
  server_time: number;
  reset_ts: number | null;
  basis: string;
}

export interface VisionStatus {
  detector: string;
  is_real_model: boolean;
  note: string;
  frames_leave_this_computer: boolean;
  min_interval_s: number;
  suggested_interval_s: number;
  price_per_image_usd: number;
  spent_usd: number;
  budget_usd: number | null;
  notice: string;
}

export interface VisionResult {
  camera_id: number;
  run_id: string;
  status: string;
  error_category: string | null;
  count: number | null;
  level: CrowdLevel;
  boxes: Array<{ id: number; box: number[]; score: number | null }>;
  frame_w: number | null;
  frame_h: number | null;
  detector: string;
  is_real_model: boolean;
  note: string;
}

export interface Explanation {
  text: string | null;
  call: LlmCall;
  disclaimer: string;
}
