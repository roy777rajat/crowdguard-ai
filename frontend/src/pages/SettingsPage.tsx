import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { fmtDateTime, label } from '../api/format';
import { Badge } from '../components/Badge';
import { Empty, ErrorBox, Loading, Notice } from '../components/States';
import { useAuth } from '../hooks/auth';
import { usePolling } from '../hooks/usePolling';
import type { AuditRow } from '../types';

export default function SettingsPage() {
  const { can } = useAuth();
  const canWrite = can('settings:write');
  const settings = usePolling(() => api.settings(), 0);
  const audit = usePolling<AuditRow[]>(() => (can('audit:read') ? api.audit() : Promise.resolve([])), 10000);
  const [form, setForm] = useState({ max_data_age_s: '', sim_speed: '', llm_budget_usd: '', lead: '' });
  const [rules, setRules] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);
  const s = settings.data;

  useEffect(() => {
    if (s) {
      setForm({
        max_data_age_s: String(s.max_data_age_s),
        sim_speed: String(s.sim_speed),
        llm_budget_usd: s.llm_budget_usd === null ? '' : String(s.llm_budget_usd),
        lead: String(s.predicted_critical_lead_min),
      });
      setRules(s.rules_enabled);
    }
  }, [s]);

  async function save(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      const body: Record<string, unknown> = {
        max_data_age_s: Number(form.max_data_age_s),
        sim_speed: Number(form.sim_speed),
        predicted_critical_lead_min: Number(form.lead),
        rules_enabled: rules,
      };
      if (form.llm_budget_usd !== '') body.llm_budget_usd = Number(form.llm_budget_usd);
      await api.updateSettings(body);
      setMsg({ kind: 'ok', text: 'Settings saved.' });
      await settings.reload();
      await audit.reload();
    } catch (err) {
      setMsg({ kind: 'error', text: err instanceof Error ? err.message : 'Could not save settings' });
    }
  }

  if (settings.loading && !s) return <Loading label="Loading settings" />;
  if (settings.error && !s) return <ErrorBox message={settings.error} onRetry={() => void settings.reload()} />;
  if (!s) return null;

  return (
    <div>
      <div className="page-head">
        <h1>Settings and audit</h1>
        {!canWrite && <span className="sub">Read-only for your role.</span>}
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Integration status</h2>
          <p>Data source: <Badge tone="info">{s.data_source}</Badge></p>
          <p>
            Model provider: <strong>{s.model_integration.active_provider}</strong> ({s.model_integration.model}){' '}
            <Badge tone={s.model_integration.is_real_model ? 'normal' : 'neutral'}>
              {s.model_integration.is_real_model ? 'real model' : 'not a real model'}
            </Badge>
          </p>
          <p>Meta adapter: {s.model_integration.meta_adapter}</p>
          <p>Credentials required to run: {s.model_integration.credentials_required ? 'yes' : 'no'}</p>
          <p>Credential present on the server: {s.model_integration.credential_set ? 'yes' : 'no'} (the value is never shown)</p>
          {s.model_integration.note && <Notice kind="error">{s.model_integration.note}</Notice>}
          {s.model_integration.is_real_model && (
            <p className="sub">Real model calls send a short instruction and a JSON summary of recorded simulated measurements to Meta, and are billed to your account. Set a budget below.</p>
          )}
          <p>Authentication: {s.auth_mode}</p>
          <p>Notifications: {s.notifications}</p>
          <p className="sub">Secrets are never shown here.</p>
        </div>

        <div className="card">
          <h2>Thresholds and rules</h2>
          {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
          <form className="stack" onSubmit={(e) => void save(e)}>
            <div className="form">
              <label className="field">Data is stale after (seconds)
                <input type="number" min={1} disabled={!canWrite} value={form.max_data_age_s} onChange={(e) => setForm({ ...form, max_data_age_s: e.target.value })} />
              </label>
              <label className="field">Warn when critical is predicted within (min)
                <input type="number" min={1} max={60} disabled={!canWrite} value={form.lead} onChange={(e) => setForm({ ...form, lead: e.target.value })} />
              </label>
              <label className="field">Simulation speed (x)
                <input type="number" min={0.5} max={20} step={0.5} disabled={!canWrite} value={form.sim_speed} onChange={(e) => setForm({ ...form, sim_speed: e.target.value })} />
              </label>
              <label className="field">Model budget (USD, blank = none)
                <input type="number" min={0} step={0.01} disabled={!canWrite} value={form.llm_budget_usd} onChange={(e) => setForm({ ...form, llm_budget_usd: e.target.value })} />
              </label>
            </div>
            <h3>Alarm rules</h3>
            {Object.entries(rules).map(([k, on]) => (
              <label key={k} className="row">
                <input type="checkbox" disabled={!canWrite} checked={on} onChange={(e) => setRules({ ...rules, [k]: e.target.checked })} style={{ minWidth: 0 }} />
                {label(k)}
              </label>
            ))}
            {canWrite && <button className="btn primary" type="submit">Save settings</button>}
            <p className="sub">Pandal capacity and warning thresholds are edited per pandal on the Pandals page.</p>
          </form>
        </div>
      </div>

      {can('audit:read') && (
        <div className="card table-wrap">
          <h2>Audit history</h2>
          {!audit.data || audit.data.length === 0 ? <Empty>No audit entries yet.</Empty> : (
            <table>
              <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Subject</th><th>Detail</th></tr></thead>
              <tbody>
                {audit.data.map((a) => (
                  <tr key={a.id}>
                    <td>{fmtDateTime(a.ts)}</td>
                    <td>{a.actor}</td>
                    <td>{a.action}</td>
                    <td>{a.entity}{a.entity_id ? ` #${a.entity_id}` : ''}</td>
                    <td className="mono">{a.detail && Object.keys(a.detail).length > 0 ? JSON.stringify(a.detail) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
