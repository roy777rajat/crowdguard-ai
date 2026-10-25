import { Link } from 'react-router-dom';
import { fmtDateTime, fmtMs } from '../api/format';
import type { Run } from '../types';
import { Empty } from './States';
import { StatusBadge } from './Badge';

interface Sel { selected: Set<string>; onToggle: (id: string) => void; onToggleAll: (ids: string[]) => void }

export default function RunsTable({ runs, names = {}, select }: { runs: Run[]; names?: Record<number, string>; select?: Sel }) {
  if (runs.length === 0) return <Empty>No workflow runs yet. Start counting on the Camera wall.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {select && (
              <th>
                <input type="checkbox" aria-label="Select all runs" checked={select.selected.size === runs.length}
                  onChange={() => select.onToggleAll(runs.map((r) => r.run_id))} />
              </th>
            )}
            <th>Run</th>
            <th>Pandal</th>
            <th>Status</th>
            <th>Started</th>
            <th className="num">Duration</th>
            <th className="num">Retries</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              {select && (
                <td>
                  <input type="checkbox" aria-label={`Select run ${r.run_id}`} checked={select.selected.has(r.run_id)}
                    onChange={() => select.onToggle(r.run_id)} />
                </td>
              )}
              <td>
                <Link className="mono" to={`/runs/${r.run_id}`}>
                  {r.run_id}
                </Link>
              </td>
              <td>{r.pandal_id !== null ? names[r.pandal_id] ?? `#${r.pandal_id}` : '—'}</td>
              <td>
                <StatusBadge status={r.status} />
              </td>
              <td>{fmtDateTime(r.started)}</td>
              <td className="num">{fmtMs(r.duration_ms)}</td>
              <td className="num">{r.retry_count}</td>
              <td>{r.error_code ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
