import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { fmt, fmtDateTime, fmtMs } from '../api/format';
import { Badge, SeverityBadge, StatusBadge } from '../components/Badge';
import { ErrorBox, Loading } from '../components/States';
import { usePolling } from '../hooks/usePolling';

export default function RunDetail() {
  const { id = '' } = useParams();
  const { data, error, loading, reload } = usePolling(() => api.run(id), 0, [id]);

  if (loading && !data) return <Loading label="Loading run" />;
  if (error && !data) return <ErrorBox message={error} onRetry={() => void reload()} />;
  if (!data) return null;

  const { run, stages, measurements, forecasts, incidents, llm_calls } = data;
  const maxMs = Math.max(1, ...stages.map((s) => s.duration_ms ?? 0));
  const failed = stages.filter((s) => s.status === 'failed');

  return (
    <div>
      <p><Link to="/runs">Back to runs</Link></p>
      <div className="page-head">
        <h1 className="mono">{run.run_id}</h1>
        <StatusBadge status={run.status} />
      </div>

      <div className="card">
        <div className="grid cols-3">
          <div className="stat"><span className="v">{fmtMs(run.duration_ms)}</span><span className="k">total duration</span></div>
          <div className="stat"><span className="v">{run.retry_count}</span><span className="k">retries</span></div>
          <div className="stat"><span className="v">{stages.length}</span><span className="k">stage attempts</span></div>
        </div>
        <p className="sub" style={{ marginTop: 8 }}>
          Trace <span className="mono">{run.trace_id}</span>; pandal #{run.pandal_id ?? '—'}, camera #{run.camera_id ?? '—'};
          started {fmtDateTime(run.started)}; source timestamp {fmtDateTime(run.source_ts)}.
        </p>
        {run.error_code && (
          <div className="alert error" role="alert">
            <strong>{run.error_code}</strong>: {run.error_message}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Stage timeline</h2>
        {stages.map((s) => (
          <div key={s.id} className={`stage ${s.status === 'failed' ? 'failed' : ''}`}>
            <div>
              {s.name} {s.attempt > 1 && <Badge tone="warning">retry {s.attempt - 1}</Badge>}
            </div>
            <div className="bar" aria-hidden="true"><span style={{ width: `${Math.max(2, ((s.duration_ms ?? 0) / maxMs) * 100)}%` }} /></div>
            <div><StatusBadge status={s.status} /> <span className="sub">{fmtMs(s.duration_ms)}</span></div>
            {s.status === 'failed' && <div className="stage-err">{s.error_code}: {s.error_message}</div>}
          </div>
        ))}
        {failed.length > 0 && <p className="sub" style={{ marginTop: 8 }}>{failed.length} failed attempt{failed.length === 1 ? '' : 's'}. Error messages are sanitized before storage.</p>}
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Measurement and forecast</h2>
          {measurements.length === 0 ? <p className="sub">No measurement was stored (the run failed before persisting).</p> : measurements.map((m) => (
            <p key={m.id}>Occupancy {fmt(m.occupancy)}, entry {fmt(m.entry_rate, 1)}/min, exit {fmt(m.exit_rate, 1)}/min <Badge tone="info">{m.source}</Badge></p>
          ))}
          {forecasts.length > 0 && <p>Forecast: {forecasts.map((f) => `+${f.horizon_min} min: ${fmt(f.value)}`).join('; ')}</p>}
        </div>
        <div className="card">
          <h2>Related incidents</h2>
          {incidents.length === 0 ? <p className="sub">None.</p> : incidents.map((i) => (
            <p key={i.id}><Link to={`/incidents/${i.id}`}>{i.title}</Link> <SeverityBadge severity={i.severity} /> <StatusBadge status={i.status} /></p>
          ))}
          <h2 style={{ marginTop: 12 }}>Related model calls</h2>
          {llm_calls.length === 0 ? <p className="sub">None.</p> : llm_calls.map((c) => (
            <p key={c.id}>{c.provider}/{c.model} <StatusBadge status={c.status} /> {fmtMs(c.latency_ms)}; tokens {c.input_tokens ?? 'not reported'}</p>
          ))}
        </div>
      </div>
    </div>
  );
}
