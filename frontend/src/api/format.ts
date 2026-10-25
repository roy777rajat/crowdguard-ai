export const fmt = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const fmtTime = (ts?: number | null): string => (ts ? new Date(ts * 1000).toLocaleTimeString() : '—');

export const fmtDateTime = (ts?: number | null): string => (ts ? new Date(ts * 1000).toLocaleString() : '—');

export const fmtMs = (ms?: number | null): string =>
  ms === null || ms === undefined ? '—' : ms < 1000 ? `${ms.toFixed(0)} ms` : `${(ms / 1000).toFixed(2)} s`;

export const fmtAge = (s?: number | null): string =>
  s === null || s === undefined ? 'no data' : s < 60 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`;

export const label = (s: string): string => s.replace(/_/g, ' ');
