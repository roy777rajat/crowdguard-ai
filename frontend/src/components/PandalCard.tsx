import { Link } from 'react-router-dom';
import { fmt, fmtAge, fmtTime } from '../api/format';
import type { DashCard } from '../types';
import { Badge, RiskBadge, StatusBadge } from './Badge';
import Ribbon from './Ribbon';

export default function PandalCard({ card, zoneId }: { card: DashCard; zoneId?: string }) {
  const { pandal, state } = card;
  const cams = zoneId ? card.cameras.filter((c) => String(c.zone_id) === zoneId) : card.cameras;
  const zones = zoneId ? card.zones.filter((z) => String(z.id) === zoneId) : card.zones;
  const q = state.quality;

  return (
    <section className="card pandal-card" aria-label={pandal.name}>
      <h2>
        {pandal.name}
        <RiskBadge risk={state.has_data ? state.risk : undefined} />
        <Badge tone={state.stale ? 'warning' : state.has_data ? 'info' : 'neutral'}>
          {state.stale ? 'data stale' : state.has_data ? 'simulated' : 'no data'}
        </Badge>
      </h2>
      <p className="sub">{pandal.location ?? 'No location set'}</p>

      <Ribbon state={state} />

      {state.has_data ? (
        <>
          <div className="facts">
            <div className="stat">
              <span className="v">
                {fmt(state.occupancy)} <span className="sub">/ {fmt(state.capacity)}</span>
              </span>
              <span className="k">estimated occupancy ({fmt(state.occupancy_pct, 1)}%)</span>
            </div>
            <div className="stat">
              <span className="v">{fmt(state.entry_rate, 1)}</span>
              <span className="k">entries per min</span>
            </div>
            <div className="stat">
              <span className="v">{fmt(state.exit_rate, 1)}</span>
              <span className="k">exits per min</span>
            </div>
            <div className="stat">
              <span className="v">{state.minutes_to_critical == null ? '—' : `${fmt(state.minutes_to_critical, 1)} min`}</span>
              <span className="k">to critical threshold</span>
            </div>
          </div>

          <div className="forecast-row" aria-label="Baseline forecast occupancy">
            {['1', '3', '5', '10'].map((h) => (
              <div key={h}>
                <span className="k">+{h} min</span>
                <strong>{fmt(state.forecast?.[h])}</strong>
              </div>
            ))}
          </div>

          <div className="row" style={{ marginTop: 12 }}>
            <span>
              Data freshness: <strong>{fmtAge(state.age_s)}</strong> old
            </span>
            <span>
              Confidence: <strong>{q.label}</strong> ({fmt(q.confidence, 2)})
            </span>
            {state.bottleneck && (
              <Badge tone={state.bottleneck.flag ? 'critical' : 'normal'}>
                exit pressure {state.bottleneck.pressure ?? '—'}: {state.bottleneck.reason}
              </Badge>
            )}
          </div>
          {q.reasons.length > 0 && <p className="sub">Data quality: {q.reasons.join(', ')}.</p>}
          <p className="sub">Last update {fmtTime(state.last_measurement_wall_ts)}. Baseline forecast, not a safety guarantee.</p>
        </>
      ) : (
        <p className="sub">No measurements yet. Start the simulation to generate simulated data.</p>
      )}

      <div className="row" style={{ marginTop: 8 }}>
        <Link to="/incidents">
          {card.active_incidents} active incident{card.active_incidents === 1 ? '' : 's'}
          {card.critical_incidents > 0 ? ` (${card.critical_incidents} critical)` : ''}
        </Link>
        <Link to="/live">Live view</Link>
      </div>

      <h3 style={{ marginTop: 12 }}>Cameras and zones</h3>
      {cams.length === 0 ? (
        <p className="sub">No cameras registered{zoneId ? ' in this zone' : ''}.</p>
      ) : (
        <ul style={{ margin: '4px 0', paddingLeft: 18 }}>
          {cams.map((c) => (
            <li key={c.id}>
              {c.name} <StatusBadge status={c.status} />{' '}
              <span className="sub">
                {zones.find((z) => z.id === c.zone_id)?.name ?? 'no zone'}; last seen {fmtTime(c.last_seen)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
