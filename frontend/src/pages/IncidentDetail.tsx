import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { fmtDateTime, label } from '../api/format';
import { Badge, SeverityBadge, StatusBadge } from '../components/Badge';
import { ErrorBox, Loading, Notice } from '../components/States';
import { useAuth } from '../hooks/auth';
import { usePolling } from '../hooks/usePolling';

export default function IncidentDetail() {
  const { id } = useParams();
  const incidentId = Number(id);
  const { can } = useAuth();
  const [note, setNote] = useState('');
  const [assignee, setAssignee] = useState('');
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);
  const { data, error, loading, reload } = usePolling(() => api.incident(incidentId), 4000, [incidentId]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: 'ok', text: ok });
      await reload();
    } catch (e) {
      setMsg({ kind: 'error', text: e instanceof Error ? e.message : 'Action failed' });
    }
  }

  if (loading && !data) return <Loading label="Loading incident" />;
  if (error && !data) return <ErrorBox message={error} onRetry={() => void reload()} />;
  if (!data) return null;

  const { incident: inc, events, run } = data;
  const writable = can('incident:write') && inc.status !== 'resolved';

  return (
    <div>
      <p><Link to="/incidents">Back to incidents</Link></p>
      <div className="page-head">
        <h1>{inc.title}</h1>
        <div className="row"><SeverityBadge severity={inc.severity} /><StatusBadge status={inc.status} /></div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Details</h2>
          <p>Rule: {label(inc.rule)}</p>
          <p>Opened {fmtDateTime(inc.created_at)}; last updated {fmtDateTime(inc.updated_at)}</p>
          <p>Resolved: {fmtDateTime(inc.resolved_at)}</p>
          <p>Assignee: {inc.assignee ?? 'nobody yet'}</p>
          <p>Repeated alarms folded into this incident: {inc.repeat_count - 1}</p>
          <h3>Triggering measurements</h3>
          <pre className="mono" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(inc.detail, null, 2)}</pre>
          {run && <p>Created by run <Link className="mono" to={`/runs/${run.run_id}`}>{run.run_id}</Link></p>}
        </div>

        <div className="card">
          <h2>Respond</h2>
          {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
          {!can('incident:write') && <p className="sub">Your role can read incidents but not change them.</p>}
          {can('incident:write') && inc.status === 'resolved' && <p className="sub">This incident is resolved and can no longer change.</p>}
          {writable && (
            <div className="stack">
              <div className="row">
                {inc.status === 'open' && <button className="btn" onClick={() => void act(() => api.transition(inc.id, 'acknowledged'), 'Acknowledged.')}>Acknowledge</button>}
                <button className="btn primary" onClick={() => { if (window.confirm('Mark this incident resolved?')) void act(() => api.transition(inc.id, 'resolved', note || undefined), 'Resolved.'); }}>Resolve</button>
              </div>
              {inc.status !== 'assigned' && (
                <div className="row">
                  <input placeholder="Assign to (name)" aria-label="Assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)} />
                  <button className="btn" disabled={!assignee.trim()} onClick={() => void act(() => api.transition(inc.id, 'assigned', undefined, assignee.trim()), 'Assigned.').then(() => setAssignee(''))}>Assign</button>
                </div>
              )}
              <div className="row">
                <input placeholder="Operator note" aria-label="Operator note" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
                <button className="btn" disabled={!note.trim()} onClick={() => void act(() => api.addNote(inc.id, note.trim()), 'Note added.').then(() => setNote(''))}>Add note</button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h2>History</h2>
        <ul className="timeline">
          {events.map((e) => (
            <li key={e.id}>
              <strong>{e.action === 'ai_explanation' ? 'AI explanation (decision support)' : label(e.action)}</strong>{' '}
              {e.from_status !== e.to_status && e.to_status && <Badge tone="info">{e.from_status ?? 'new'} to {e.to_status}</Badge>}
              <div className="sub">{fmtDateTime(e.ts)} by {e.actor}</div>
              {e.note && <div>{e.note}</div>}
              {e.action === 'ai_explanation' && (
                <div className="sub">Describes recorded values only. For authorized human review; not an instruction.</div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
