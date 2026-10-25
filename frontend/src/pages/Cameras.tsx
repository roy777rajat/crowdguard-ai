import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { fmtDateTime } from '../api/format';
import { Badge, StatusBadge } from '../components/Badge';
import { Empty, ErrorBox, Loading, Notice } from '../components/States';
import { useAuth } from '../hooks/auth';
import { usePolling } from '../hooks/usePolling';

export default function Cameras() {
  const { can } = useAuth();
  const canWrite = can('registry:write');
  const [filter, setFilter] = useState('');
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);
  const [form, setForm] = useState({ pandal_id: '', zone_id: '', name: '', source_type: 'simulated', source_ref: '' });
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);

  const pandals = usePolling(() => api.pandals(), 0);
  const cams = usePolling(() => api.cameras(filter), 4000, [filter]);

  const zonesFor = (pid: string) => (pandals.data ?? []).find((p) => String(p.id) === pid)?.zones ?? [];
  const pandalName = (id: number) => (pandals.data ?? []).find((p) => p.id === id)?.name ?? `#${id}`;
  const zoneName = (pid: number, zid: number | null) =>
    zid === null ? '—' : (pandals.data ?? []).find((p) => p.id === pid)?.zones?.find((z) => z.id === zid)?.name ?? `#${zid}`;

  async function run(fn: () => Promise<unknown>, ok: string): Promise<boolean> {
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: 'ok', text: ok });
      await cams.reload();
      return true;
    } catch (e) {
      setMsg({ kind: 'error', text: e instanceof Error ? e.message : 'Request failed' });
      return false;
    }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    const ok = await run(
      () =>
        api.createCamera({
          pandal_id: Number(form.pandal_id),
          zone_id: form.zone_id ? Number(form.zone_id) : null,
          name: form.name,
          source_type: form.source_type,
          source_ref: form.source_type === 'simulated' ? null : form.source_ref,
        }),
      `Registered camera “${form.name}”. Registered does not mean connected.`,
    );
    if (ok) setForm({ ...form, name: '', source_ref: '' });
  }

  if (cams.loading && !cams.data) return <Loading label="Loading cameras" />;
  if (cams.error && !cams.data) return <ErrorBox message={cams.error} onRetry={() => void cams.reload()} />;

  return (
    <div>
      <div className="page-head">
        <h1>Camera registry</h1>
        {!canWrite && <span className="sub">Read-only for your role.</span>}
      </div>
      <p className="sub">
        A camera is logical until frames are actually processed. “Registered” never means connected. Source details must not
        contain credentials; the server rejects them.
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}

      {canWrite && (
        <div className="card">
          <h2>Register a camera</h2>
          <form className="form" onSubmit={(e) => void create(e)}>
            <label className="field">
              Pandal
              <select value={form.pandal_id} onChange={(e) => setForm({ ...form, pandal_id: e.target.value, zone_id: '' })} required>
                <option value="">Choose…</option>
                {(pandals.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label className="field">
              Zone
              <select value={form.zone_id} onChange={(e) => setForm({ ...form, zone_id: e.target.value })} disabled={!form.pandal_id}>
                <option value="">No zone</option>
                {zonesFor(form.pandal_id).map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
              </select>
            </label>
            <label className="field">Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} /></label>
            <label className="field">
              Source
              <select value={form.source_type} onChange={(e) => setForm({ ...form, source_type: e.target.value })}>
                <option value="simulated">Simulated source</option>
                <option value="local_video">Local video file (gallery folder)</option>
                <option value="live_stream">Live stream URL (http/https)</option>
              </select>
            </label>
            {form.source_type !== 'simulated' && (
              <label className="field">
                {form.source_type === 'local_video' ? 'File name in the gallery folder, e.g. entry.mp4' : 'Stream URL (no credentials)'}
                <input value={form.source_ref} onChange={(e) => setForm({ ...form, source_ref: e.target.value })} required />
              </label>
            )}
            <button className="btn primary" type="submit">Register camera</button>
          </form>
          {form.source_type !== 'simulated' && (
            <p className="sub">
              Shown on the Camera wall. A live stream must be playable by the browser and allow cross-origin reads for
              counting; otherwise it is displayed but not counted. RTSP is not supported by browsers.
            </p>
          )}
        </div>
      )}

      <div className="spacer" />
      <div className="card">
        <div className="form">
          <label className="field">
            Pandal
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">All pandals</option>
              {(pandals.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
        </div>
        {!cams.data || cams.data.length === 0 ? (
          <Empty>No cameras registered{filter ? ' for this pandal' : ''}.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Camera</th><th>Pandal / zone</th><th>Source</th><th>Status</th><th>Last seen</th><th>Last error</th><th /></tr>
              </thead>
              <tbody>
                {cams.data.map((c) => (
                  <tr key={c.id}>
                    <td>
                      {editing?.id === c.id ? (
                        <form
                          className="row"
                          onSubmit={(e) => {
                            e.preventDefault();
                            void run(() => api.updateCamera(c.id, { name: editing.name }), 'Camera renamed.').then((ok) => ok && setEditing(null));
                          }}
                        >
                          <input value={editing.name} onChange={(e) => setEditing({ id: c.id, name: e.target.value })} aria-label="Camera name" />
                          <button className="btn small primary" type="submit">Save</button>
                          <button className="btn small" type="button" onClick={() => setEditing(null)}>Cancel</button>
                        </form>
                      ) : (
                        <>
                          {c.name} {!c.active && <Badge>deactivated</Badge>}
                        </>
                      )}
                    </td>
                    <td>{pandalName(c.pandal_id)} / {zoneName(c.pandal_id, c.zone_id)}<div className="sub">{(c.mode ?? 'active') === 'passive' ? 'Passive' : 'Active'}</div></td>
                    <td>{c.source_type === 'simulated' ? 'Simulated' : c.source_type === 'local_video' ? `Local video: ${c.source_ref ?? ''}` : 'Live stream'}<div className="sub">{c.connection_note}</div></td>
                    <td><StatusBadge status={c.status} /></td>
                    <td>{fmtDateTime(c.last_seen)}</td>
                    <td>{c.last_error ?? '—'}</td>
                    <td>
                      {canWrite && c.active === 1 && (
                        <div className="row">
                          <button className="btn small" onClick={() => setEditing({ id: c.id, name: c.name })}>Rename</button>
                          <button className="btn small danger" onClick={() => { if (window.confirm(`Deactivate ${c.name}?`)) void run(() => api.deactivateCamera(c.id), 'Camera deactivated.'); }}>Deactivate</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
