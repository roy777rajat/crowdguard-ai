import { api } from '../api/client';
import ScopePicker from '../components/ScopePicker';
import { Empty, ErrorBox, Loading, Notice } from '../components/States';
import { usePolling } from '../hooks/usePolling';
import { useScope } from '../hooks/useScope';
import { LEVEL_COLOR, LEVEL_TEXT } from '../components/CameraTile';
import '../styles/camera-wall.css';

type Pt = { ts: number; count: number };
interface Line { name: string; color: string; pts: Pt[] }

const PALETTE = ['#ff7a3d', '#ffc65a', '#e0457b', '#9bd36a'];

function LineChart({ title, lines, windowS, now, hlines }: {
  title: string; lines: Line[]; windowS: number; now: number; hlines?: Array<{ y: number; label: string; color: string }>;
}) {
  const W = 640; const H = 220; const L = 38; const R = 10; const T = 10; const B = 26;
  const t0 = now - windowS;
  const maxY = Math.max(5, ...lines.flatMap((l) => l.pts.map((p) => p.count)), ...(hlines ?? []).map((h) => h.y)) * 1.1;
  const x = (ts: number) => L + ((ts - t0) / windowS) * (W - L - R);
  const y = (v: number) => T + (1 - v / maxY) * (H - T - B);
  const hasData = lines.some((l) => l.pts.length > 0);
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={L} x2={W - R} y1={y(maxY * f / 1.1)} y2={y(maxY * f / 1.1)} stroke="currentColor" opacity="0.15" />
            <text x={L - 4} y={y(maxY * f / 1.1) + 4} textAnchor="end" fontSize="11" fill="currentColor">{Math.round(maxY * f / 1.1)}</text>
          </g>
        ))}
        {[0, 0.5, 1].map((f) => (
          <text key={f} x={L + f * (W - L - R)} y={H - 6} textAnchor={f === 0 ? 'start' : f === 1 ? 'end' : 'middle'} fontSize="11" fill="currentColor">
            {f === 1 ? 'now' : `-${Math.round(windowS * (1 - f) / 60)} min`}
          </text>
        ))}
        {(hlines ?? []).map((h) => (
          <g key={h.label}>
            <line x1={L} x2={W - R} y1={y(h.y)} y2={y(h.y)} stroke={h.color} strokeDasharray="5 4" />
            <text x={W - R} y={y(h.y) - 3} textAnchor="end" fontSize="11" fill={h.color}>{h.label} {h.y}</text>
          </g>
        ))}
        {lines.map((l) => l.pts.length > 0 && (
          <polyline key={l.name} fill="none" stroke={l.color} strokeWidth="2"
            points={l.pts.filter((p) => p.ts >= t0).map((p) => `${x(p.ts).toFixed(1)},${y(p.count).toFixed(1)}`).join(' ')} />
        ))}
        {!hasData && <text x={W / 2} y={H / 2} textAnchor="middle" fill="currentColor" opacity="0.7">No counts in this window yet. Start the Camera wall.</text>}
      </svg>
      <div className="legend">
        {lines.map((l) => <span key={l.name}><i style={{ background: l.color }} />{l.name}</span>)}
      </div>
    </figure>
  );
}

export default function Dashboard() {
  const scope = useScope();
  const wallQ = usePolling(
    () => (scope.pandalId === null ? Promise.resolve(null) : api.wall(scope.pandalId, 1800)),
    3000,
    [scope.pandalId],
  );
  const wall = wallQ.data;

  if (scope.loading && scope.locations.length === 0) return <Loading label="Loading locations" />;
  if (scope.error) return <ErrorBox message={scope.error} onRetry={() => window.location.reload()} />;

  return (
    <div className="wall">
      <div className="page-head"><h1>Dashboard</h1></div>
      <ScopePicker scope={scope} />
      {!scope.pandal ? <Empty>Choose a location with a pandal.</Empty> : wallQ.loading && !wall ? <Loading label="Loading counts" /> : wall && (
        <>
          <div className="wall-banner" style={{ ['--lv' as string]: LEVEL_COLOR[wall.totals.level] }} role="status">
            <div className="big">{wall.pandal.name}: {LEVEL_TEXT[wall.totals.level]}</div>
            <div className="stat"><b>{wall.totals.count ?? '—'}</b> people in view (sum of zones)</div>
            <div className="stat"><b>{wall.totals.pct_of_threshold === null ? '—' : `${wall.totals.pct_of_threshold.toFixed(0)}%`}</b> of threshold</div>
            <div className="stat"><b>{wall.totals.pct_criticality === null ? '—' : `${wall.totals.pct_criticality.toFixed(0)}%`}</b> criticality</div>
          </div>
          {wallQ.error && <Notice kind="error">{wallQ.error}</Notice>}
          <div className="chart-grid">
            {wall.zones.map((z, i) => (
              <LineChart key={z.zone.id} title={`${z.zone.name}${z.camera ? ` / ${z.camera.name}` : ''}`} windowS={wall.window_s} now={wall.server_time}
                lines={[{ name: z.zone.name, color: PALETTE[i % PALETTE.length], pts: z.series }]}
                hlines={[{ y: z.limits.warn, label: 'warn', color: '#a15c00' }, { y: z.limits.critical, label: 'critical', color: '#b3261e' }]} />
            ))}
            <LineChart title="All cameras: sum" windowS={wall.window_s} now={wall.server_time}
              lines={[{ name: 'Sum of zones', color: 'currentColor', pts: wall.sum_series }, ...wall.zones.map((z, i) => ({ name: z.zone.name, color: PALETTE[i % PALETTE.length], pts: z.series }))]}
              hlines={[{ y: wall.totals.warn, label: 'warn', color: '#a15c00' }, { y: wall.totals.critical, label: 'critical', color: '#b3261e' }]} />
          </div>
          <p className="wall-foot">
            Counts are people in view at each sampled frame over the last {Math.round(wall.window_s / 60)} minutes. {wall.basis}{' '}
            Simulated and test sources are labelled on the Camera wall. Decision support, not an instruction.
          </p>
        </>
      )}
    </div>
  );
}
