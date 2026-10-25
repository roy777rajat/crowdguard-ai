import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, type PandalDetail } from '../api/client';
import { fmt, fmtDateTime } from '../api/format';
import { Badge, RiskBadge, SeverityBadge, StatusBadge } from '../components/Badge';
import RunsTable from '../components/RunsTable';
import { Empty, ErrorBox, Loading, Notice } from '../components/States';
import { useAuth } from '../hooks/auth';
import { usePolling } from '../hooks/usePolling';
import InfoTip from '../components/InfoTip';

const blank = { name: '', locationId: '', z1: 'Entry gate', z2: 'Main pandal', z3: 'Exit gate', maxPeople: '40', warn: '70', critical: '90' };

export default function Pandals() {
  const { can } = useAuth();
  const canWrite = can('registry:write');
  const [form, setForm] = useState(blank);
  const [selected, setSelected] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);

  const list = usePolling(() => api.pandals(true), 5000);
  const detail = usePolling<PandalDetail | null>(
    () => (selected === null ? Promise.resolve(null) : api.pandal(selected)),
    4000,
    [selected],
  );

  const [edit, setEdit] = useState({ name: '', location: '', capacity: '', warn: '', critical: '' });
  const [camForm, setCamForm] = useState({ zone_id: '', name: '' });
  const [zoneEdits, setZoneEdits] = useState<Record<number, { name: string; max: string }>>({});
  const locs = usePolling(() => api.locations(), 0);
  const d = detail.data;

  useEffect(() => {
    if (d) {
      setEdit({
        name: d.pandal.name,
        location: d.pandal.location ?? '',
        capacity: String(d.pandal.capacity),
        warn: String(Math.round(d.pandal.warn_pct * 100)),
        critical: String(Math.round(d.pandal.critical_pct * 100)),
      });
    }
    // Only reset the edit form when a different pandal is opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d?.pandal.id]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: 'ok', text: ok });
      await list.reload();
      await detail.reload();
    } catch (e) {
      setMsg({ kind: 'error', text: e instanceof Error ? e.message : 'Request failed' });
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    void run(
      () =>
        api.createLocationPandal(Number(form.locationId), {
          name: form.name,
          zone_names: [form.z1, form.z2, form.z3],
          max_people: Number(form.maxPeople),
          warn_pct: Number(form.warn) / 100,
          critical_pct: Number(form.critical) / 100,
        }),
      `Created pandal “${form.name}” with 3 zones and a camera in each.`,
    ).then(() => setForm(blank));
  }

  if (list.loading && !list.data) return <Loading label="Loading pandals" />;
  if (list.error && !list.data) return <ErrorBox message={list.error} onRetry={() => void list.reload()} />;

  return (
    <div>
      <div className="page-head">
        <h1>Pandal registry
          <InfoTip title="Pandal registry">
            <p>The list of Puja committees (pandals) under each Kolkata location. Each has exactly 3 zones and a camera per zone. Add, rename, edit limits, switch cameras on or off, or delete.</p>
            <p>Deleting a pandal permanently removes its zones, cameras, counts, workflow runs and incidents.</p>
          </InfoTip>
        </h1>
        {!canWrite && <span className="sub">Read-only for your role.</span>}
        {canWrite && (
          <button className="btn small danger" onClick={() => {
            if (window.prompt('This permanently deletes ALL pandals, zones, cameras, counts, runs and incidents. Type DELETE to confirm.') === 'DELETE') {
              void run(() => api.clearRegistry(), 'All pandals and cameras deleted.').then(() => setSelected(null));
            }
          }}>Delete all pandals and cameras</button>
        )}
      </div>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}

      {canWrite && (
        <div className="card">
          <h2>Register a pandal</h2>
          <p className="sub">Every pandal has exactly 3 zones, and each zone gets one camera (change it to passive on the Camera wall if needed).</p>
          <form className="form" onSubmit={create}>
            <label className="field">Location
              <select value={form.locationId} onChange={(e) => setForm({ ...form, locationId: e.target.value })} required>
                <option value="">Choose…</option>
                {(locs.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            </label>
            <label className="field">Pandal name (association, must be unique)<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} /></label>
            <label className="field">Zone 1<input value={form.z1} onChange={(e) => setForm({ ...form, z1: e.target.value })} required maxLength={80} /></label>
            <label className="field">Zone 2<input value={form.z2} onChange={(e) => setForm({ ...form, z2: e.target.value })} required maxLength={80} /></label>
            <label className="field">Zone 3<input value={form.z3} onChange={(e) => setForm({ ...form, z3: e.target.value })} required maxLength={80} /></label>
            <label className="field">Max people per zone<input type="number" min={1} max={5000} value={form.maxPeople} onChange={(e) => setForm({ ...form, maxPeople: e.target.value })} required /></label>
            <label className="field">Warning at (% of max)<input type="number" min={1} max={149} value={form.warn} onChange={(e) => setForm({ ...form, warn: e.target.value })} required /></label>
            <label className="field">Critical at (% of max)<input type="number" min={1} max={150} value={form.critical} onChange={(e) => setForm({ ...form, critical: e.target.value })} required /></label>
            <button className="btn primary" type="submit">Create pandal</button>
          </form>
        </div>
      )}

      <div className="spacer" />
      <div className="card">
        {!list.data || list.data.length === 0 ? (
          <Empty>No pandals registered yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Name</th><th>Location</th><th className="num">Capacity</th><th className="num">Warn / critical</th><th>Zones</th><th>Cameras</th><th>Status</th><th /></tr>
              </thead>
              <tbody>
                {list.data.map((p) => (
                  <tr key={p.id}>
                    <td>{p.name}</td>
                    <td>{(locs.data ?? []).find((l) => l.id === p.location_id)?.name ?? p.location ?? '—'}</td>
                    <td className="num">{fmt(p.capacity)}</td>
                    <td className="num">{Math.round(p.warn_pct * 100)}% / {Math.round(p.critical_pct * 100)}%</td>
                    <td>{p.zones?.length ?? 0}</td>
                    <td>{p.cameras?.length ?? 0}</td>
                    <td>{p.active ? <Badge tone="normal">active</Badge> : <Badge>deactivated</Badge>}</td>
                    <td><button className="btn small" onClick={() => setSelected(p.id)}>Open</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selected !== null && (
        <div className="card">
          {detail.loading && !d ? (
            <Loading label="Loading pandal" />
          ) : detail.error && !d ? (
            <ErrorBox message={detail.error} onRetry={() => void detail.reload()} />
          ) : d ? (
            <>
              <div className="page-head">
                <h2>{d.pandal.name}</h2>
                <div className="row">
                  <RiskBadge risk={d.state.has_data ? d.state.risk : undefined} />
                  {canWrite && (
                    <button className="btn danger small" onClick={() => {
                      if (window.confirm(`Permanently delete ${d.pandal.name} with its zones, cameras, counts, runs and incidents?`)) {
                        void run(() => api.deletePandal(d.pandal.id), 'Pandal deleted.').then(() => setSelected(null));
                      }
                    }}>Delete</button>
                  )}
                  {canWrite && d.pandal.active === 1 && (
                    <button
                      className="btn danger small"
                      onClick={() => { if (window.confirm(`Deactivate ${d.pandal.name}? It will stop receiving simulated data.`)) void run(() => api.deactivatePandal(d.pandal.id), 'Pandal deactivated.'); }}
                    >
                      Deactivate
                    </button>
                  )}
                  <button className="btn small" onClick={() => setSelected(null)}>Close</button>
                </div>
              </div>

              {canWrite && (
                <form
                  className="form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(
                      () => api.updatePandal(d.pandal.id, {
                        name: edit.name,
                        location: edit.location || null,
                        capacity: Number(edit.capacity),
                        warn_pct: Number(edit.warn) / 100,
                        critical_pct: Number(edit.critical) / 100,
                      }),
                      'Pandal updated.',
                    );
                  }}
                >
                  <label className="field">Pandal name<input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} required maxLength={120} /></label>
                  <label className="field">Location note<input value={edit.location} onChange={(e) => setEdit({ ...edit, location: e.target.value })} /></label>
                  <label className="field">Capacity<input type="number" min={1} value={edit.capacity} onChange={(e) => setEdit({ ...edit, capacity: e.target.value })} /></label>
                  <label className="field">Warning %<input type="number" value={edit.warn} onChange={(e) => setEdit({ ...edit, warn: e.target.value })} /></label>
                  <label className="field">Critical %<input type="number" value={edit.critical} onChange={(e) => setEdit({ ...edit, critical: e.target.value })} /></label>
                  <button className="btn primary" type="submit">Save changes</button>
                </form>
              )}

              <h3 style={{ marginTop: 16 }}>Zones</h3>
              {d.zones.length === 0 ? <p className="sub">No zones yet.</p> : (
                d.zones.map((z) => {
                  const ze = zoneEdits[z.id] ?? { name: z.name, max: String(z.max_people ?? 40) };
                  return (
                    <form key={z.id} className="form" onSubmit={(e) => {
                      e.preventDefault();
                      void run(() => api.updateZone(z.id, { name: ze.name, max_people: Number(ze.max) }), 'Zone updated.');
                    }}>
                      <label className="field">Zone name<input value={ze.name} disabled={!canWrite} maxLength={80}
                        onChange={(e) => setZoneEdits({ ...zoneEdits, [z.id]: { ...ze, name: e.target.value } })} /></label>
                      <label className="field">Max people<input type="number" min={1} max={5000} value={ze.max} disabled={!canWrite}
                        onChange={(e) => setZoneEdits({ ...zoneEdits, [z.id]: { ...ze, max: e.target.value } })} /></label>
                      {canWrite && <button className="btn small" type="submit">Save zone</button>}
                    </form>
                  );
                })
              )}

              <h3 style={{ marginTop: 16 }}>Cameras</h3>
              {d.cameras.length === 0 ? <p className="sub">No cameras yet.</p> : (
                <ul className="cam-list">{d.cameras.map((c) => (
                  <li key={c.id}>
                    {c.name} ({c.mode ?? 'active'}) {c.active ? <Badge tone="normal">enabled</Badge> : <Badge>deactivated</Badge>}{' '}
                    <StatusBadge status={c.status} /> <span className="sub">{d.zones.find((z) => z.id === c.zone_id)?.name ?? 'no zone'}</span>
                    {canWrite && (
                      <span className="row">
                        <button className="btn small" onClick={() => void run(() => api.setCameraActive(c.id, !c.active), c.active ? 'Camera deactivated.' : 'Camera activated.')}>
                          {c.active ? 'Deactivate' : 'Activate'}
                        </button>
                        <button className="btn small danger" onClick={() => { if (window.confirm(`Permanently delete camera ${c.name} and its counts?`)) void run(() => api.deleteCamera(c.id), 'Camera deleted.'); }}>Delete</button>
                      </span>
                    )}
                  </li>
                ))}</ul>
              )}
              {canWrite && (
                <form className="form" onSubmit={(e) => {
                  e.preventDefault();
                  void run(() => api.createCamera({ pandal_id: d.pandal.id, zone_id: Number(camForm.zone_id), name: camForm.name, source_type: 'simulated' }),
                    'Camera added. Choose its video on the Camera wall.').then(() => setCamForm({ zone_id: '', name: '' }));
                }}>
                  <label className="field">Zone
                    <InfoTip title="Add a camera">A zone can have one enabled camera. Deactivate or delete the old one first to replace it. Pick its video on the Camera wall.</InfoTip>
                    <select value={camForm.zone_id} onChange={(e) => setCamForm({ ...camForm, zone_id: e.target.value })} required>
                      <option value="">Choose…</option>
                      {d.zones.filter((z) => !d.cameras.some((c) => c.zone_id === z.id && c.active)).map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
                    </select>
                  </label>
                  <label className="field">Camera name<input value={camForm.name} onChange={(e) => setCamForm({ ...camForm, name: e.target.value })} required maxLength={120} /></label>
                  <button className="btn" type="submit">Add camera</button>
                </form>
              )}

              <h3 style={{ marginTop: 16 }}>Incident history</h3>
              {d.incidents.length === 0 ? <p className="sub">No incidents.</p> : (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Opened</th></tr></thead>
                    <tbody>
                      {d.incidents.slice(0, 10).map((i) => (
                        <tr key={i.id}>
                          <td><Link to={`/incidents/${i.id}`}>{i.title}</Link></td>
                          <td><SeverityBadge severity={i.severity} /></td>
                          <td><StatusBadge status={i.status} /></td>
                          <td>{fmtDateTime(i.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <h3 style={{ marginTop: 16 }}>Recent workflow runs</h3>
              <RunsTable runs={d.runs} names={{ [d.pandal.id]: d.pandal.name }} />
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
