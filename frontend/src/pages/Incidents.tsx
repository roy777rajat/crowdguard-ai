import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { fmtDateTime, label } from '../api/format';
import { SeverityBadge, StatusBadge } from '../components/Badge';
import { Empty, ErrorBox, Loading } from '../components/States';
import { usePolling } from '../hooks/usePolling';
import { useAuth } from '../hooks/auth';
import DeleteBar from '../components/DeleteBar';
import InfoTip from '../components/InfoTip';

export default function Incidents() {
  const [status, setStatus] = useState('');
  const [severity, setSeverity] = useState('');
  const [pandalId, setPandalId] = useState('');
  const [date, setDate] = useState('');
  const { can } = useAuth();
  const canDelete = can('settings:write');
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);

  const pandals = usePolling(() => api.pandals(true), 0);
  const { data, error, loading, reload } = usePolling(
    () => api.incidents({ status, severity, pandal_id: pandalId, date }),
    4000,
    [status, severity, pandalId, date],
  );
  const name = (id: number) => (pandals.data ?? []).find((p) => p.id === id)?.name ?? `#${id}`;

  async function del(b: { ids?: string[]; all?: boolean }) {
    setErr(null);
    try {
      await api.deleteIncidents(b);
      setSel(new Set());
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Delete failed');
    }
  }
  const allIds = (data ?? []).map((i) => String(i.id));

  return (
    <div>
      <div className="page-head">
        <h1>Incidents
          <InfoTip title="Incidents">
            <p>An incident is opened when a zone passes its warning or critical limit. Repeats are folded into the open incident. Staff acknowledge, assign and resolve it.</p>
            <p>Deleting removes the incident and its history for good (use it to start fresh).</p>
          </InfoTip>
        </h1>
        {canDelete && (
          <DeleteBar noun="incidents" selected={sel.size} total={allIds.length}
            info="'Delete selected' removes the ticked rows. 'Delete all' removes every incident and its notes, for all pandals, not only the ones filtered on screen."
            onDeleteSelected={() => void del({ ids: [...sel] })} onDeleteAll={() => void del({ all: true })} />
        )}
      </div>
      {err && <p className="alert error" role="alert">{err}</p>}
      <p className="sub">
        Alarms from configured rules open an incident. Repeated identical alarms are folded into the open incident instead of
        creating duplicates. Simulated data.
      </p>
      <div className="card">
        <div className="form">
          <label className="field">Status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any</option>
              {['open', 'acknowledged', 'assigned', 'resolved'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="field">Severity
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="">Any</option>
              {['warning', 'critical'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="field">Pandal
            <select value={pandalId} onChange={(e) => setPandalId(e.target.value)}>
              <option value="">All</option>
              {(pandals.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="field">Opened on (UTC)
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
        </div>
      </div>
      <div className="spacer" />
      {loading && !data ? (
        <Loading label="Loading incidents" />
      ) : error && !data ? (
        <ErrorBox message={error} onRetry={() => void reload()} />
      ) : !data || data.length === 0 ? (
        <Empty>No incidents match. Run the arrival surge scenario to generate some.</Empty>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>{canDelete && <th><input type="checkbox" aria-label="Select all incidents" checked={allIds.length > 0 && sel.size === allIds.length} onChange={() => setSel(sel.size === allIds.length ? new Set() : new Set(allIds))} /></th>}<th>Incident</th><th>Pandal</th><th>Rule</th><th>Severity</th><th>Status</th><th>Assignee</th><th className="num">Repeats</th><th>Opened</th></tr>
            </thead>
            <tbody>
              {data.map((i) => (
                <tr key={i.id}>
                  {canDelete && <td><input type="checkbox" aria-label={`Select incident ${i.id}`} checked={sel.has(String(i.id))} onChange={() => { const n = new Set(sel); const k = String(i.id); if (n.has(k)) n.delete(k); else n.add(k); setSel(n); }} /></td>}
                  <td><Link to={`/incidents/${i.id}`}>{i.title}</Link></td>
                  <td>{name(i.pandal_id)}</td>
                  <td>{label(i.rule)}</td>
                  <td><SeverityBadge severity={i.severity} /></td>
                  <td><StatusBadge status={i.status} /></td>
                  <td>{i.assignee ?? '—'}</td>
                  <td className="num">{i.repeat_count}</td>
                  <td>{fmtDateTime(i.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
