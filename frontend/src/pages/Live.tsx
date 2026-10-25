import { useState } from 'react';
import InfoTip from '../components/InfoTip';
import ScopePicker from '../components/ScopePicker';
import { useScope } from '../hooks/useScope';
import { api } from '../api/client';
import { fmt, fmtAge, fmtTime } from '../api/format';
import { Badge, RiskBadge } from '../components/Badge';
import Ribbon from '../components/Ribbon';
import { Empty, ErrorBox, Loading, Notice } from '../components/States';
import TrendChart from '../components/TrendChart';
import { useAuth } from '../hooks/auth';
import { usePolling } from '../hooks/usePolling';
import type { Explanation, Series } from '../types';

const SCENARIO_HELP: Record<string, string> = {
  normal_flow: 'Steady arrivals and exits.',
  arrival_surge: 'Arrivals ramp up sharply.',
  reduced_exit: 'Exit throughput collapses; local bottleneck signal fires.',
  stale_sensor: 'The sensor stops reporting after a few minutes.',
};

const ERROR_HINTS: Record<string, string> = {
  authentication: 'The credential was rejected. Check META_MODEL_API_KEY in .env and restart the backend.',
  billing: 'Billing is not set up on the Meta account.',
  forbidden: 'This credential has no access to the model or feature.',
  invalid_request: 'Meta rejected the request format.',
  not_found: 'Model or endpoint not found.',
  throttled: 'Rate limited. Wait a moment and try again.',
  overloaded: 'The service is overloaded. Try again shortly.',
  server_error: 'Meta reported a server error. Try again.',
  gateway_timeout: 'The request timed out at Meta.',
  timeout: 'No reply in time from Meta.',
  network: 'Could not reach api.meta.ai. Check internet, proxy or firewall.',
  incomplete: 'The reply was cut off before finishing.',
  refusal: 'The model declined to answer.',
  empty_response: 'The reply contained no text.',
  bad_response: 'The reply was not in the expected format.',
  unknown: 'Unexpected error inside the backend.',
};

export default function Live() {
  const { can } = useAuth();
  const scope = useScope();
  const pandalId = scope.pandalId;
  const [scenario, setScenario] = useState('arrival_surge');
  const [speed, setSpeed] = useState(2);
  const [actionError, setActionError] = useState<string | null>(null);
  const [explain, setExplain] = useState<Explanation | null>(null);
  const [explaining, setExplaining] = useState(false);

  const sim = usePolling(() => api.sim(pandalId ?? undefined), 2000, [pandalId]);

  const series = usePolling<Series | null>(
    () => (pandalId === null ? Promise.resolve(null) : api.series(pandalId, 60)),
    2000,
    [pandalId],
  );

  async function act(fn: () => Promise<unknown>) {
    setActionError(null);
    try {
      await fn();
      await sim.reload();
      await series.reload();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Action failed');
    }
  }

  async function runExplain() {
    if (pandalId === null) return;
    setExplaining(true);
    setActionError(null);
    try {
      setExplain(await api.explain(pandalId));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not generate explanation');
    } finally {
      setExplaining(false);
    }
  }

  if (scope.loading && scope.locations.length === 0) return <Loading label="Loading locations" />;
  if (scope.error) return <ErrorBox message={scope.error} onRetry={() => window.location.reload()} />;

  const s = sim.data;
  const d = series.data;
  const st = d?.state;
  const pts = d?.points ?? [];

  return (
    <div>
      <div className="page-head">
        <h1>Live monitoring
          <InfoTip title="Live monitoring and prediction">
            <p>Shows one pandal at a time: how many people are inside, how fast people enter and leave, and a short forecast.</p>
            <p><b>How the prediction works:</b> a plain rule-based engine (not AI) takes the current occupancy and the recent entry and exit rates, assumes they stay the same, and projects occupancy a few minutes ahead (5 and 10 minutes are used in the explanation). "Minutes to warning/critical" is when that line would cross the pandal's limits. It is an estimate and it is wrong if the flow changes.</p>
            <p><b>Data health</b> (fresh, stale) is separate from risk: stale data suppresses occupancy alarms. The <b>exit bottleneck</b> signal compares people wanting to leave with the exit capacity of the zones.</p>
            <p>The numbers here come from the built-in simulator (clearly labelled), run separately for the pandal you pick. The AI explanation only words the engine's numbers; it never computes them. Decision support, not an instruction.</p>
          </InfoTip>
        </h1>
        <Badge tone="info">simulated measurements</Badge>
      </div>

      <div className="card">
        <ScopePicker scope={scope} />
        {!scope.pandal && <Empty>No pandal in this location. Add one on the Pandals page.</Empty>}
        <div className="form">
          {can('sim:control') ? (
            <>
              <label className="field">
                Scenario
                <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
                  {(s?.scenarios ?? Object.keys(SCENARIO_HELP)).map((x) => (
                    <option key={x} value={x}>{x.replace(/_/g, ' ')}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                Speed (x)
                <input type="number" min={0.5} max={20} step={0.5} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
              </label>
              <button className="btn primary" disabled={pandalId === null} onClick={() => void act(() => api.simStart(scenario, speed, pandalId ?? undefined))}>Start (resets this pandal)</button>
              <button className="btn" disabled={!s?.running} onClick={() => void act(() => api.simPause(pandalId ?? undefined))}>Pause</button>
              <button className="btn" disabled={!s || s.running || s.minute === 0} onClick={() => void act(() => api.simResume(pandalId ?? undefined))}>Resume</button>
              <button className="btn danger" onClick={() => { if (window.confirm('Reset clears the simulated measurements of this pandal only. Continue?')) void act(() => api.simReset(pandalId ?? undefined)); }}>Reset</button>
            </>
          ) : (
            <span className="sub">Your role can watch the simulation but not control it.</span>
          )}
        </div>
        <p className="sub" style={{ marginTop: 8 }}>
          {SCENARIO_HELP[scenario]} This pandal's simulation is {s?.running ? 'running' : 'not running'}, minute {s?.minute ?? 0}.
        </p>
        {actionError && <Notice kind="error">{actionError}</Notice>}
      </div>

      <div className="spacer" />
      {series.loading && !d ? (
        <Loading label="Loading series" />
      ) : series.error && !d ? (
        <ErrorBox message={series.error} onRetry={() => void series.reload()} />
      ) : st ? (
        <div className="grid cols-2">
          <div className="card">
            <h2>Risk <RiskBadge risk={st.has_data ? st.risk : undefined} /></h2>
            <Ribbon state={st} />
            {st.has_data ? (
              <div className="facts">
                <div className="stat"><span className="v">{fmt(st.occupancy)}</span><span className="k">occupancy of {fmt(st.capacity)}</span></div>
                <div className="stat"><span className="v">{st.minutes_to_warning == null ? '—' : `${fmt(st.minutes_to_warning, 1)} min`}</span><span className="k">predicted to cross warning</span></div>
                <div className="stat"><span className="v">{st.minutes_to_critical == null ? '—' : `${fmt(st.minutes_to_critical, 1)} min`}</span><span className="k">predicted to cross critical</span></div>
              </div>
            ) : (
              <Empty>No measurements yet. Start the simulation.</Empty>
            )}
            <p className="sub">Predicted crossing times assume entry and exit rates stay constant. They are estimates.</p>
          </div>

          <div className="card">
            <h2>Data health (separate from risk)</h2>
            <div className="facts">
              <div className="stat"><span className="v">{st.stale ? 'Stale' : st.has_data ? 'Fresh' : 'None'}</span><span className="k">freshness: {fmtAge(st.age_s)} old</span></div>
              <div className="stat"><span className="v">{st.quality.label}</span><span className="k">confidence {fmt(st.quality.confidence, 2)}</span></div>
              <div className="stat"><span className="v">{fmtTime(st.last_measurement_wall_ts)}</span><span className="k">last successful update</span></div>
            </div>
            {st.quality.reasons.length > 0 && <Notice kind="error">{st.quality.reasons.join('; ')}. Occupancy alarms are suppressed while data is stale.</Notice>}
            <h3 style={{ marginTop: 12 }}>Local exit bottleneck (separate signal)</h3>
            {st.bottleneck ? (
              <p>
                <Badge tone={st.bottleneck.flag ? 'critical' : 'normal'}>{st.bottleneck.flag ? 'bottleneck risk' : 'within capacity'}</Badge>{' '}
                pressure {st.bottleneck.pressure ?? '—'}: {st.bottleneck.reason}. Venue-wide occupancy alone does not show an exit is safe.
              </p>
            ) : (
              <p className="sub">No exit data yet.</p>
            )}
          </div>

          <div className="card">
            <h2>Occupancy</h2>
            <TrendChart
              title="Occupancy over simulated minutes"
              series={[{ name: 'Occupancy', color: 'var(--brand)', values: pts.map((p) => p.occupancy) }]}
              hlines={[
                { y: d!.warn_threshold, label: 'warning', color: 'var(--warning)' },
                { y: d!.critical_threshold, label: 'critical', color: 'var(--critical)' },
              ]}
              xLabels={pts.map((p) => (p.minute === null ? null : `min ${p.minute}`))}
            />
          </div>

          <div className="card">
            <h2>Flow rates (people per minute)</h2>
            <TrendChart
              title="Entry and exit rate"
              series={[
                { name: 'Entry', color: 'var(--info)', values: pts.map((p) => p.entry_rate) },
                { name: 'Exit', color: 'var(--warning)', values: pts.map((p) => p.exit_rate) },
              ]}
              xLabels={pts.map((p) => (p.minute === null ? null : `min ${p.minute}`))}
            />
          </div>
        </div>
      ) : null}

      <div className="card">
        <h2>AI explanation</h2>
        <p className="sub">
          Describes recorded measurements only. It cannot change occupancy or forecasts, and it is decision support for
          authorized human review, not an instruction. The provider and model used are shown with each result.
        </p>
        <button className="btn" disabled={explaining || !st?.has_data} onClick={() => void runExplain()}>
          {explaining ? 'Generating…' : 'Explain current state'}
        </button>
        {explain && (
          <div style={{ marginTop: 12 }}>
            {explain.text === null ? (
              <Notice kind="error">
                The model call failed: <strong>{explain.call.error_category ?? 'unknown'}</strong>.{' '}
                {ERROR_HINTS[explain.call.error_category ?? ''] ?? 'See Model calls for details.'}
              </Notice>
            ) : (
              <p>{explain.text}</p>
            )}
            <div className="row">
              {explain.text !== null && (
                <Badge tone={explain.call.evaluation?.grounded ? 'normal' : 'warning'}>
                  {explain.call.evaluation?.grounded ? 'numbers match recorded values' : 'ungrounded numbers found'}
                </Badge>
              )}
              <span className="sub">
                {explain.call.provider}/{explain.call.model}; tokens {explain.call.input_tokens ?? 'not reported'}; cost{' '}
                {explain.call.est_cost_usd ?? 'unknown'}
              </span>
            </div>
            <p className="sub">{explain.disclaimer}</p>
          </div>
        )}
      </div>
    </div>
  );
}
