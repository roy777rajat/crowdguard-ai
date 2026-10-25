import { api } from '../api/client';
import { fmt, fmtMs, label } from '../api/format';
import { Badge } from '../components/Badge';
import { ErrorBox, Loading } from '../components/States';
import { usePolling } from '../hooks/usePolling';
import { useState } from 'react';
import { useAuth } from '../hooks/auth';
import InfoTip from '../components/InfoTip';

function Group({ title, values }: { title: string; values: Record<string, number | string | null> }) {
  return (
    <div className="card">
      <h2>{title}</h2>
      <table>
        <tbody>
          {Object.entries(values).map(([k, v]) => (
            <tr key={k}>
              <th scope="row">{label(k)}</th>
              <td className="num">
                {v === null ? '—' : typeof v === 'string' ? v : k.endsWith('_ms') ? fmtMs(v) : k.includes('rate') ? fmt(v, 3) : fmt(v, 2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function System() {
  const metrics = usePolling(() => api.systemMetrics(), 5000);
  const health = usePolling(() => api.health(), 5000);
  const { can } = useAuth();
  const [msg, setMsg] = useState<string | null>(null);
  async function clearHealth() {
    if (!window.confirm('Clear the API request/latency figures and camera status flags?')) return;
    try {
      const r = await api.clearSystem();
      setMsg(r.note);
      await metrics.reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Could not clear');
    }
  }

  if (metrics.loading && !metrics.data) return <Loading label="Loading system health" />;
  if (metrics.error && !metrics.data) return <ErrorBox message={metrics.error} onRetry={() => void metrics.reload()} />;
  const m = metrics.data;
  if (!m) return null;

  return (
    <div>
      <div className="page-head">
        <h1>System health
          <InfoTip title="System health">
            <p>How the app itself is doing: API speed and errors, workflow run durations and failures, camera status and model-API errors.</p>
            <p>API and camera figures are cleared here. Workflow figures are computed from the run history, so delete runs on the Workflow runs page to reset those too.</p>
          </InfoTip>
        </h1>
        <div className="row">
          {can('settings:write') && <button className="btn small danger" onClick={() => void clearHealth()}>Clear health data</button>}
          <Badge tone={health.data?.status === 'ok' ? 'normal' : 'critical'}>API {health.data?.status ?? 'unknown'}</Badge>
          <Badge tone={health.data?.database === 'ok' ? 'normal' : 'critical'}>database {health.data?.database ?? 'unknown'}</Badge>
        </div>
      </div>
      {msg && <p className="alert ok" role="status">{msg}</p>}
      <p className="sub">Local metrics from this backend process and the SQLite store. Nothing is sent to CloudWatch or any external service.</p>
      <div className="grid cols-2">
        <Group title="API requests (since the backend started)" values={m.api} />
        <Group title="Ingestion (last 500 runs)" values={m.ingestion} />
        <Group title="Workflow duration" values={m.workflow} />
        <Group title="Cameras" values={m.cameras} />
        <Group title="Model API" values={m.model_api} />
        <Group title="Storage" values={m.storage} />
      </div>
    </div>
  );
}
