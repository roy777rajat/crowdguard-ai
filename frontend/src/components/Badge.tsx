import type { ReactNode } from 'react';
import { label } from '../api/format';

export type Tone = 'normal' | 'warning' | 'critical' | 'info' | 'neutral';

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

/** Text is always shown, so meaning never depends on colour alone. */
export function RiskBadge({ risk }: { risk?: string }) {
  if (!risk) return <Badge>no data</Badge>;
  return <Badge tone={risk as Tone}>{risk}</Badge>;
}

const STATUS_TONE: Record<string, Tone> = {
  succeeded: 'normal', live: 'normal', resolved: 'normal', receiving_simulated: 'normal', success: 'normal',
  running: 'info', open: 'warning', acknowledged: 'info', assigned: 'info', registered: 'neutral',
  partial: 'warning', degraded: 'warning', stale: 'warning', no_data: 'neutral',
  failed: 'critical', error: 'critical', inactive: 'neutral',
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? 'neutral'}>{label(status)}</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={severity === 'critical' ? 'critical' : severity === 'warning' ? 'warning' : 'info'}>{severity}</Badge>;
}
