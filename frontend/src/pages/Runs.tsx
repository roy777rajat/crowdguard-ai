import { useState } from 'react';
import { api } from '../api/client';
import RunsTable from '../components/RunsTable';
import { ErrorBox, Loading } from '../components/States';
import { usePolling } from '../hooks/usePolling';
import { useAuth } from '../hooks/auth';
import DeleteBar from '../components/DeleteBar';
import InfoTip from '../components/InfoTip';

export default function Runs() {
  const [status, setStatus] = useState('');
  const [pandalId, setPandalId] = useState('');
  const [date, setDate] = useState('');
  const { can } = useAuth();
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);

  const pandals = usePolling(() => api.pandals(true), 0);
  const { data, error, loading, reload } = usePolling(
    () => api.runs({ status, pandal_id: pandalId, date }),
    4000,
    [status, pandalId, date],
  );
  const names: Record<number, string> = {};
  (pandals.data ?? []).forEach((p) => (names[p.id] = p.name));

  const canDelete = can('settings:write');
  async function del(b: { ids?: string[]; all?: boolean }) {
    setErr(null);
    try {
      await api.deleteRuns(b);
      setSel(new Set());
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  return (
    <div>
      <div className="page-head">
        <h1>Workflow explorer
          <InfoTip title="Workflow runs">
            <p>Each camera snapshot is processed as a run with stages: capture, detect (SAM 3.1), persist, evaluate. A run shows whether the model answered, how long it took and any error.</p>
            <p>Runs are stored, so history survives restarts. Delete them to start with a clean history; counts on the Camera wall are cleared with them.</p>
          </InfoTip>
        </h1>
        {canDelete && (
          <DeleteBar noun="workflow runs" selected={sel.size} total={(data ?? []).length}
            info="Removes the chosen runs with their stages. 'Delete all' removes every run for every pandal and clears stored camera counts. Your estimated SAM spend is remembered, so the budget cap is not reset."
            onDeleteSelected={() => void del({ ids: [...sel] })} onDeleteAll={() => void del({ all: true })} />
        )}
      </div>
      {err && <p className="alert error" role="alert">{err}</p>}
      <p className="sub">Runs are stored, so history survives restarts.</p>
      <div className="card">
        <div className="form">
          <label className="field">Status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any</option>
              {['succeeded', 'partial', 'failed', 'running'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="field">Pandal
            <select value={pandalId} onChange={(e) => setPandalId(e.target.value)}>
              <option value="">All</option>
              {(pandals.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="field">Started on (UTC)
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
        </div>
      </div>
      <div className="spacer" />
      <div className="card">
        {loading && !data ? <Loading label="Loading runs" /> : error && !data ? <ErrorBox message={error} onRetry={() => void reload()} /> : <RunsTable runs={data ?? []} names={names}
          select={canDelete ? { selected: sel, onToggle: (id) => { const n = new Set(sel); if (n.has(id)) n.delete(id); else n.add(id); setSel(n); }, onToggleAll: (ids) => setSel(sel.size === ids.length ? new Set() : new Set(ids)) } : undefined} />}
      </div>
    </div>
  );
}
