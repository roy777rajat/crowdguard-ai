import type { PandalState } from '../types';

const RISK_VAR: Record<string, string> = {
  normal: 'var(--normal)',
  warning: 'var(--warning)',
  critical: 'var(--critical)',
};

/**
 * Occupancy ribbon: the bar is current occupancy; the hatched extension is the baseline 10-minute
 * projection; the white tick is the 5-minute projection; the two vertical rules are the warning and
 * critical thresholds. The projection is a constant-rate estimate, not a guarantee.
 */
export default function Ribbon({ state }: { state: PandalState }) {
  const cap = state.capacity;
  const occ = state.occupancy ?? 0;
  const f5 = state.forecast?.['5'];
  const f10 = state.forecast?.['10'];
  const max = Math.max(cap * 1.1, occ, f10 ?? 0, state.critical_threshold * 1.05);
  const x = (v: number) => Math.max(0, Math.min(1000, (v / max) * 1000));
  const color = RISK_VAR[state.risk ?? ''] ?? 'var(--muted)';
  const hatch = `hatch-${state.pandal_id}`;
  const aria = state.has_data
    ? `Occupancy ${Math.round(occ)} of ${cap}. Warning at ${Math.round(state.warn_threshold)}, critical at ${Math.round(state.critical_threshold)}. Simulated.`
    : 'No measurements yet';

  return (
    <svg className="ribbon" viewBox="0 0 1000 58" role="img" aria-label={aria}>
      <defs>
        <pattern id={hatch} width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="4" height="8" fill={color} opacity="0.45" />
        </pattern>
      </defs>
      <rect x="0" y="12" width="1000" height="20" rx="4" fill="var(--surface-2)" stroke="var(--line)" />
      {state.has_data && (
        <>
          {f10 !== undefined && (
            <rect
              x={x(Math.min(occ, f10))}
              y="12"
              width={Math.abs(x(f10) - x(occ))}
              height="20"
              fill={`url(#${hatch})`}
            />
          )}
          <rect x="0" y="12" width={x(occ)} height="20" rx="4" fill={color} />
          {f5 !== undefined && <line x1={x(f5)} x2={x(f5)} y1="9" y2="35" stroke="var(--ink)" strokeWidth="2" strokeDasharray="3 2" />}
        </>
      )}
      <line x1={x(state.warn_threshold)} x2={x(state.warn_threshold)} y1="6" y2="40" stroke="var(--warning)" strokeWidth="2" />
      <line x1={x(state.critical_threshold)} x2={x(state.critical_threshold)} y1="6" y2="40" stroke="var(--critical)" strokeWidth="2" />
      <text x={x(state.warn_threshold)} y="54" textAnchor="middle">warn {Math.round(state.warn_threshold)}</text>
      <text x={Math.min(x(state.critical_threshold), 960)} y="54" textAnchor="middle">critical {Math.round(state.critical_threshold)}</text>
      <text x="0" y="54">0</text>
    </svg>
  );
}
