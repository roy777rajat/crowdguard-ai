import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { fmt, fmtDateTime, fmtMs } from '../api/format';
import { Badge, StatusBadge } from '../components/Badge';
import { Empty, ErrorBox, Loading } from '../components/States';
import { usePolling } from '../hooks/usePolling';
import type { LlmGroup } from '../types';
import { useState } from 'react';
import { useAuth } from '../hooks/auth';
import InfoTip from '../components/InfoTip';

function GroupTable({ title, groups }: { title: string; groups: Record<string, LlmGroup> }) {
  const rows = Object.entries(groups);
  return (
    <div className="card table-wrap">
      <h2>{title}</h2>
      {rows.length === 0 ? <p className="sub">No calls yet.</p> : (
        <table>
          <thead><tr><th>{title.replace('By ', '')}</th><th className="num">Requests</th><th className="num">Errors</th><th className="num">p50</th><th className="num">p95</th><th className="num">Tokens in / out</th><th className="num">Est. cost</th></tr></thead>
          <tbody>
            {rows.map(([k, g]) => (
              <tr key={k}>
                <td>{k}</td>
                <td className="num">{g.requests}</td>
                <td className="num">{g.errors}</td>
                <td className="num">{fmtMs(g.latency_p50_ms)}</td>
                <td className="num">{fmtMs(g.latency_p95_ms)}</td>
                <td className="num">{g.input_tokens === null ? 'not reported' : `${fmt(g.input_tokens)} / ${fmt(g.output_tokens)}`}</td>
                <td className="num">{g.est_cost_usd === null ? 'unknown' : `$${fmt(g.est_cost_usd, 4)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function Llm() {
  const summary = usePolling(() => api.llmSummary(), 5000);
  const calls = usePolling(() => api.llmCalls(), 5000);
  const { can } = useAuth();
  const [clearMsg, setClearMsg] = useState<string | null>(null);
  async function clearAll() {
    if (!window.confirm('Permanently delete ALL model-call history (tokens, latency, cost estimates)? This cannot be undone.')) return;
    try {
      const r = await api.clearLlm();
      setClearMsg(`Deleted ${r.deleted} call records. ${r.note}`);
      await summary.reload();
      await calls.reload();
    } catch (e) {
      setClearMsg(e instanceof Error ? e.message : 'Could not clear');
    }
  }

  if (summary.loading && !summary.data) return <Loading label="Loading model telemetry" />;
  if (summary.error && !summary.data) return <ErrorBox message={summary.error} onRetry={() => void summary.reload()} />;
  const s = summary.data;
  if (!s) return null;
  const b = s.budget;

  return (
    <div>
      <div className="page-head">
        <h1>Model calls
          <InfoTip title="Model calls">
            <p>Every request to an AI model: which model, how long it took (latency), tokens in and out, the estimated cost and whether it failed. Used to watch spend and reliability.</p>
            <p>Costs here are estimates from the app's own log, not your provider's invoice.</p>
          </InfoTip>
        </h1>
        {can('settings:write') && <button className="btn small danger" onClick={() => void clearAll()}>Delete all model-call history</button>}
        <Badge tone="info">pricing data: {s.pricing_version}</Badge>
      </div>
      <p className="sub">
        Values come from what the provider reported. If tokens or prices are unavailable they show as “not reported” or
        “unknown”; nothing is estimated silently. Raw prompts are never stored, only a hash and length.
      </p>

      <div className="card">
        <div className="grid cols-3">
          <div className="stat"><span className="v">{fmt(s.requests)}</span><span className="k">requests</span></div>
          <div className="stat"><span className="v">{fmtMs(s.latency_p50_ms)}</span><span className="k">latency p50</span></div>
          <div className="stat"><span className="v">{fmtMs(s.latency_p95_ms)}</span><span className="k">latency p95</span></div>
          <div className="stat"><span className="v">{s.error_rate === null ? '—' : `${fmt(s.error_rate * 100, 1)}%`}</span><span className="k">error rate ({s.errors} of {s.requests})</span></div>
          <div className="stat"><span className="v">{s.input_tokens === null ? 'not reported' : `${fmt(s.input_tokens)} / ${fmt(s.output_tokens)}`}</span><span className="k">tokens in / out ({s.calls_missing_tokens} calls missing counts)</span></div>
          <div className="stat"><span className="v">{s.est_cost_usd === null ? 'unknown' : `$${fmt(s.est_cost_usd, 4)}`}</span><span className="k">estimated cost ({s.calls_missing_cost} calls without a price)</span></div>
        </div>
        <p style={{ marginTop: 12 }}>
          Budget: <Badge tone={b.status === 'exceeded' ? 'critical' : b.status === 'ok' ? 'normal' : 'neutral'}>{b.status}</Badge>{' '}
          limit {b.limit_usd === null ? 'not set' : `$${fmt(b.limit_usd, 2)}`}. <span className="sub">{b.note}</span>
        </p>
      </div>

      <div className="grid cols-2">
        <GroupTable title="By model" groups={s.by_model} />
        <GroupTable title="By operation" groups={s.by_operation} />
      </div>

      <div className="card table-wrap">
        <h2>Recent calls</h2>
        {!calls.data || calls.data.length === 0 ? <Empty>No model calls yet. They happen when an incident opens or when you request an explanation.</Empty> : (
          <table>
            <thead><tr><th>When</th><th>Model</th><th>Operation</th><th>Status</th><th className="num">Latency</th><th>Grounded</th><th>Run</th></tr></thead>
            <tbody>
              {calls.data.map((c) => (
                <tr key={c.id}>
                  <td>{fmtDateTime(c.started)}</td>
                  <td>{c.provider}/{c.model}</td>
                  <td>{c.operation} <span className="sub">{c.prompt_version}</span></td>
                  <td><StatusBadge status={c.status} /> {c.error_category}</td>
                  <td className="num">{fmtMs(c.latency_ms)}</td>
                  <td>{c.evaluation ? (c.evaluation.grounded ? 'yes' : 'no') : '—'}</td>
                  <td>{c.run_id ? <Link className="mono" to={`/runs/${c.run_id}`}>{c.run_id}</Link> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
