import type {
  AuditRow, Camera, Dashboard, Explanation, Incident, IncidentEvent, LlmCall, LlmSummary, Pandal,
  PandalState, Run, Series, Settings, Simulation, Stage, User, VisionResult, VisionStatus, WallView, Zone, Location,
} from '../types';

// DEMO ONLY: the session token is kept in localStorage. Production should use Cognito with
// short-lived tokens and a safer storage strategy.
const TOKEN_KEY = 'cg_token';

export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (t: string): void => localStorage.setItem(TOKEN_KEY, t),
  clear: (): void => localStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const tok = tokenStore.get();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch('/api' + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText || `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg?: string }) => d.msg ?? '').join('; ');
    } catch {
      /* keep status text */
    }
    if (res.status === 401) window.dispatchEvent(new Event('cg-unauthorized'));
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : '';
}

export interface PandalDetail {
  pandal: Pandal;
  zones: Zone[];
  cameras: Camera[];
  state: PandalState;
  incidents: Incident[];
  runs: Run[];
}

export interface RunDetail {
  run: Run;
  stages: Stage[];
  measurements: Array<{ id: number; occupancy: number; entry_rate: number; exit_rate: number; source: string }>;
  forecasts: Array<{ id: number; horizon_min: number; value: number }>;
  incidents: Incident[];
  llm_calls: LlmCall[];
}

export interface SystemMetrics {
  api: Record<string, number | null>;
  ingestion: Record<string, number | null>;
  workflow: Record<string, number | null>;
  cameras: Record<string, number | null>;
  model_api: Record<string, number | null>;
  storage: Record<string, string>;
}

export const api = {
  health: () => request<{ status: string; database: string; auth: string; data_source: string }>('GET', '/health'),
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>('POST', '/auth/login', { username, password }),
  register: (username: string, password: string, display_name?: string) =>
    request<{ user: User; note: string }>('POST', '/auth/register', { username, password, display_name }),
  logout: () => request<{ ok: boolean }>('POST', '/auth/logout'),
  me: () => request<{ user: User }>('GET', '/auth/me'),

  dashboard: (f: { pandal_id?: string; status?: string; date?: string }) =>
    request<Dashboard>('GET', '/dashboard' + qs(f)),

  pandals: (includeInactive = false) =>
    request<Pandal[]>('GET', '/pandals' + qs({ include_inactive: includeInactive ? 'true' : undefined })),
  pandal: (id: number) => request<PandalDetail>('GET', `/pandals/${id}`),
  createPandal: (b: Record<string, unknown>) => request<Pandal>('POST', '/pandals', b),
  updatePandal: (id: number, b: Record<string, unknown>) => request<Pandal>('PUT', `/pandals/${id}`, b),
  deactivatePandal: (id: number) => request<{ ok: boolean }>('DELETE', `/pandals/${id}`),
  createZone: (pandalId: number, b: { name: string; exit_capacity_per_min: number }) =>
    request<Zone>('POST', `/pandals/${pandalId}/zones`, b),
  series: (id: number, limit = 60) => request<Series>('GET', `/pandals/${id}/series?limit=${limit}`),
  explain: (id: number) => request<Explanation>('POST', `/pandals/${id}/explain`),

  cameras: (pandalId?: string) => request<Camera[]>('GET', '/cameras' + qs({ pandal_id: pandalId })),
  createCamera: (b: Record<string, unknown>) => request<Camera>('POST', '/cameras', b),
  updateCamera: (id: number, b: Record<string, unknown>) => request<Camera>('PUT', `/cameras/${id}`, b),
  deactivateCamera: (id: number) => request<{ ok: boolean }>('DELETE', `/cameras/${id}`),

  sim: (pandalId?: number) => request<Simulation>('GET', '/simulation' + qs({ pandal_id: pandalId })),
  simStart: (scenario: string, speed: number, pandalId?: number) =>
    request<Simulation>('POST', '/simulation/start', { scenario, speed, pandal_id: pandalId ?? null }),
  simPause: (pandalId?: number) => request<Simulation>('POST', '/simulation/pause' + qs({ pandal_id: pandalId })),
  simResume: (pandalId?: number) => request<Simulation>('POST', '/simulation/resume' + qs({ pandal_id: pandalId })),
  simReset: (pandalId?: number) => request<Simulation>('POST', '/simulation/reset' + qs({ pandal_id: pandalId })),

  incidents: (f: { status?: string; pandal_id?: string; severity?: string; date?: string }) =>
    request<Incident[]>('GET', '/incidents' + qs(f)),
  incident: (id: number) =>
    request<{ incident: Incident; events: IncidentEvent[]; run: Run | null }>('GET', `/incidents/${id}`),
  transition: (id: number, to: string, note?: string, assignee?: string) =>
    request<Incident>('POST', `/incidents/${id}/transition`, { to, note, assignee }),
  addNote: (id: number, note: string) => request<IncidentEvent>('POST', `/incidents/${id}/notes`, { note }),

  runs: (f: { status?: string; pandal_id?: string; date?: string }) => request<Run[]>('GET', '/runs' + qs(f)),
  run: (id: string) => request<RunDetail>('GET', `/runs/${id}`),

  llmSummary: () => request<LlmSummary>('GET', '/llm/summary'),
  llmCalls: () => request<LlmCall[]>('GET', '/llm/calls?limit=50'),
  systemMetrics: () => request<SystemMetrics>('GET', '/system/metrics'),
  settings: () => request<Settings>('GET', '/settings'),
  updateSettings: (b: Record<string, unknown>) => request<Settings>('PUT', '/settings', b),
  audit: () => request<AuditRow[]>('GET', '/audit?limit=100'),

  deletePandal: (id: number) => request<{ ok: boolean }>('DELETE', `/pandals/${id}/permanent`),
  clearRegistry: () => request<{ ok: boolean }>('POST', '/registry/clear', { confirm: 'DELETE' }),
  deleteCamera: (id: number) => request<{ ok: boolean }>('DELETE', `/cameras/${id}/permanent`),
  setCameraActive: (id: number, active: boolean) => request<Camera>('POST', `/cameras/${id}/active`, { active }),
  deleteRuns: (b: { ids?: string[]; all?: boolean }) => request<{ ok: boolean; deleted: number }>('POST', '/runs/delete', b),
  deleteIncidents: (b: { ids?: string[]; all?: boolean }) => request<{ ok: boolean; deleted: number }>('POST', '/incidents/delete', b),
  clearLlm: () => request<{ ok: boolean; deleted: number; note: string }>('POST', '/llm/clear', { confirm: 'DELETE' }),
  clearSystem: () => request<{ ok: boolean; note: string }>('POST', '/system/clear'),

  visionStatus: () => request<VisionStatus>('GET', '/vision/status'),
  locations: () => request<Location[]>('GET', '/locations'),
  pandalsIn: (locationId?: number) =>
    request<Pandal[]>('GET', '/pandals' + qs({ location_id: locationId })),
  createLocationPandal: (locationId: number, b: { name: string; zone_names: string[]; max_people: number; warn_pct: number; critical_pct: number }) =>
    request<{ pandal: Pandal; zones: Zone[]; cameras: Camera[] }>('POST', `/locations/${locationId}/pandals`, b),
  updateZone: (id: number, b: { name?: string; max_people?: number }) => request<Zone>('PUT', `/zones/${id}`, b),
  wall: (pandalId: number, windowS?: number) =>
    request<WallView>('GET', `/vision/pandals/${pandalId}/wall` + qs({ window_s: windowS })),
  resetWall: (pandalId: number) => request<{ ok: boolean }>('POST', `/vision/pandals/${pandalId}/reset`),
  visionMedia: () => request<Array<{ name: string; bytes: number }>>('GET', '/vision/gallery'),
  /** Videos need the bearer token, which a plain <video src> cannot send, so fetch them as a blob. */
  visionVideoUrl: async (name: string): Promise<string> => {
    const tok = tokenStore.get();
    const res = await fetch('/api/vision/gallery/' + encodeURIComponent(name), {
      headers: tok ? { Authorization: `Bearer ${tok}` } : {},
    });
    if (!res.ok) throw new ApiError(res.status, res.status === 404 ? `${name} is not in the gallery folder` : `HTTP ${res.status}`);
    return URL.createObjectURL(await res.blob());
  },
  visionAnalyze: async (cameraId: number, frame: Blob, demoTruth?: number): Promise<VisionResult> => {
    const tok = tokenStore.get();
    const q = demoTruth === undefined ? '' : `?demo_truth=${demoTruth}`;
    const res = await fetch(`/api/vision/cameras/${cameraId}/analyze${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'image/jpeg', ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      body: frame,
    });
    if (!res.ok) {
      let msg = res.statusText || `HTTP ${res.status}`;
      try {
        const j = await res.json();
        if (typeof j.detail === 'string') msg = j.detail;
      } catch {
        /* keep status text */
      }
      throw new ApiError(res.status, msg);
    }
    return (await res.json()) as VisionResult;
  },
};
