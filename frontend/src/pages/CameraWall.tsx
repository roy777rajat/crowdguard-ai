import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import CameraTile, { LEVEL_COLOR, LEVEL_TEXT, type RunState } from '../components/CameraTile';
import InfoTip from '../components/InfoTip';
import ScopePicker from '../components/ScopePicker';
import { Empty, ErrorBox, Loading } from '../components/States';
import { useAuth } from '../hooks/auth';
import { usePolling } from '../hooks/usePolling';
import { useScope } from '../hooks/useScope';
import type { CrowdLevel } from '../types';
import '../styles/camera-wall.css';

const INTERVALS = [2, 3, 5, 10, 20, 30, 60];
const pct = (v: number | null) => (v === null ? '—' : `${v.toFixed(0)}%`);

export default function CameraWall() {
  const { can } = useAuth();
  const canAnalyze = can('sim:control');
  const canRegister = can('registry:write');
  const canBudget = can('settings:write');
  const wallRef = useRef<HTMLDivElement>(null);
  const scope = useScope();
  const status = usePolling(() => api.visionStatus(), 5000);
  const gallery = usePolling(() => api.visionMedia(), 10000); // picks up new files without a refresh
  const wallQ = usePolling(
    () => (scope.pandalId === null ? Promise.resolve(null) : api.wall(scope.pandalId)),
    2500,
    [scope.pandalId],
  );
  const [run, setRun] = useState<RunState>('stopped');
  const [resetToken, setResetToken] = useState(0);
  const [every, setEvery] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [budgetText, setBudgetText] = useState('');
  const [budgetMsg, setBudgetMsg] = useState<string | null>(null);

  // Changing pandal always stops counting, so nothing is billed by accident.
  useEffect(() => { setRun('stopped'); }, [scope.pandalId]);

  async function doReset() {
    if (scope.pandalId === null) return;
    setMsg(null);
    try {
      await api.resetWall(scope.pandalId);
      setRun('stopped');
      setResetToken((n) => n + 1);
      await wallQ.reload();
      setMsg('Counts cleared. Press Start to begin again.');
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Reset failed');
    }
  }

  async function saveBudget() {
    setBudgetMsg(null);
    try {
      await api.updateSettings({ vision_budget_usd: Number(budgetText) });
      setBudgetText('');
      await status.reload();
    } catch (e) {
      setBudgetMsg(e instanceof Error ? e.message : 'Could not save');
    }
  }

  const wall = wallQ.data;
  const st = status.data;
  const everyS = Math.max(1, every ?? st?.suggested_interval_s ?? 3);
  const activeCams = wall ? wall.zones.filter((z) => z.camera && (z.camera.mode ?? 'active') === 'active').length : 0;
  const worst: CrowdLevel = wall?.totals.level ?? 'unknown';
  const headline = { crowded: 'Crowded: check the red zone', busy: 'Some zones are busy', quiet: 'All zones quiet', unknown: 'Waiting for counts', passive: 'Waiting for counts' }[worst];
  const perHour = st ? (activeCams * 3600 / everyS) * st.price_per_image_usd : 0;

  if (scope.loading && scope.locations.length === 0) return <Loading label="Loading locations" />;
  if (scope.error) return <ErrorBox message={scope.error} onRetry={() => window.location.reload()} />;

  return (
    <div className="wall fit" ref={wallRef}>
      <div className="wall-top">
        <h1>Camera wall
          <InfoTip title="Camera wall">
            <p>Live view of the 3 zone cameras of one pandal. Each snapshot is sent to the people-counting model (Meta SAM 3.1); the count is saved, a workflow run is recorded and warnings are raised when limits are crossed.</p>
            <p>It counts only while this page is open and started. Frames that show people are sent to Meta, so get venue approval first.</p>
          </InfoTip>
        </h1>
        <ScopePicker scope={scope} compact />
        {st?.is_real_model && st.budget_usd !== null && (
          <div className="budget-mini">
            <label>
              Budget
              <InfoTip title="Budget (spend cap)" align="right">
                <p>The most you allow this app to spend on image counting. Each analysed picture costs about ${st.price_per_image_usd}, so cost = pictures × price.</p>
                <p>Why use it: a camera snapshot every few seconds adds up fast. Now: {activeCams} active camera(s) every {everyS}s ≈ ${perHour.toFixed(2)} per hour while running.</p>
                <p>When estimated spend reaches the budget, counting stops until an administrator raises it. It is an estimate from this app's own log, not your Meta invoice.</p>
              </InfoTip>
            </label>
            <span className="spent">${st.spent_usd.toFixed(2)} / ${st.budget_usd.toFixed(2)}</span>
            {canBudget && (
              <>
                <input type="number" min="0" step="0.5" value={budgetText} onChange={(e) => setBudgetText(e.target.value)}
                  aria-label="New budget in dollars" placeholder="$" />
                <button className="btn small" disabled={budgetText === ''} onClick={() => void saveBudget()}>Set</button>
              </>
            )}
            {budgetMsg && <span className="err">{budgetMsg}</span>}
          </div>
        )}
      </div>

      {!scope.pandal ? (
        <Empty>No pandal here yet. Add one on the Pandals page.</Empty>
      ) : (
        <>
          <div className="wall-banner slim" style={{ ['--lv' as string]: LEVEL_COLOR[worst] }} role="status" aria-live="polite">
            <div className="big">{scope.pandal.name}: {headline}</div>
            <div className="stat"><b>{wall?.totals.count ?? '—'}</b> people</div>
            <div className="stat"><b>{wall?.totals.cameras_reporting ?? 0}</b>/{activeCams} cameras reporting</div>
          </div>

          <div className="wall-controls" role="group" aria-label="Camera wall controls">
            {canAnalyze ? (
              <>
                <button className="btn small" disabled={run === 'running' || activeCams === 0} onClick={() => setRun('running')}>
                  {run === 'paused' ? 'Resume' : 'Start all'}
                </button>
                <button className="btn small" disabled={run !== 'running'} onClick={() => setRun('paused')}>Pause</button>
                <button className="btn small" disabled={run === 'stopped'} onClick={() => setRun('stopped')}>Stop all</button>
                <button className="btn small" onClick={() => void doReset()}>Reset</button>
                <InfoTip title="Start, Pause, Stop, Reset">
                  <p><b>Start all</b> starts all 3 cameras: videos play and a snapshot is analysed at every interval.</p>
                  <p><b>Pause</b> freezes videos and counting; <b>Resume</b> continues.</p>
                  <p><b>Stop all</b> stops all 3 and rewinds the videos. <b>Reset</b> clears this pandal's counts on the wall (history for other pandals is untouched).</p>
                </InfoTip>
                <label>
                  Snapshot every
                  <select value={every ?? ''} onChange={(e) => setEvery(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Default ({st?.suggested_interval_s ?? 3}s)</option>
                    {INTERVALS.map((s) => <option key={s} value={s}>{s} s</option>)}
                  </select>
                  <InfoTip title="Snapshot frequency">
                    <p>How often one picture per camera is sent to the model. Shorter = fresher counts but proportionally higher cost. Longer = cheaper but slower to notice a crowd building.</p>
                    <p>Counts older than 60 s are shown as stale, so intervals above 30 s will often look stale.</p>
                  </InfoTip>
                </label>
              </>
            ) : (
              <span>Your role can watch the wall. Only administrators and operators can start counting.</span>
            )}
            <span className="state-pill">{run === 'running' ? 'Running' : run === 'paused' ? 'Paused' : 'Stopped'}</span>
            {msg && <span className="sub">{msg}</span>}
            <button className="btn small push" onClick={() => void (document.fullscreenElement ? document.exitFullscreen() : wallRef.current?.requestFullscreen())}>
              Full screen
            </button>
          </div>

          {wallQ.loading && !wall ? <Loading label="Loading cameras" /> : wall && (
            <>
              <div className="wall-grid">
                {wall.zones.map((z) => (
                  <CameraTile key={z.zone.id} zoneWall={z} run={run} resetToken={resetToken} everyMs={everyS * 1000}
                    canAnalyze={canAnalyze} canRegister={canRegister} realModel={!!st?.is_real_model}
                    gallery={(gallery.data ?? []).map((m) => m.name)} onChanged={() => void wallQ.reload()} />
                ))}
              </div>

              <div className="count-grids">
                {wall.zones.map((z) => (
                  <table key={z.zone.id} className="count-grid" style={{ ['--lv' as string]: LEVEL_COLOR[z.level] }}>
                    <caption>{wall.pandal.name} / {z.camera?.name ?? z.zone.name}</caption>
                    <tbody>
                      <tr><th scope="row">Total count</th><td>{z.count ?? 'No count'}</td></tr>
                      <tr><th scope="row">% of threshold</th><td>{pct(z.pct_of_threshold)}</td></tr>
                      <tr><th scope="row">% criticality</th><td>{pct(z.pct_criticality)} <span className="lv-chip">{LEVEL_TEXT[z.level]}</span></td></tr>
                    </tbody>
                  </table>
                ))}
                <table className="count-grid total" style={{ ['--lv' as string]: LEVEL_COLOR[wall.totals.level] }}>
                  <caption>
                    {wall.pandal.name} / All cameras
                    <InfoTip title="Counts and limits" align="right">
                      <p><b>Total count</b>: people counted in the latest snapshot of that camera. The last grid adds the cameras up.</p>
                      <p><b>% of threshold</b> = count ÷ warning limit. 100% means the warning limit is reached.</p>
                      <p><b>% criticality</b> = count ÷ critical limit. 100% means the critical limit is reached.</p>
                      <p>Limits come from each zone's "max people" and the pandal's warn/critical % (Pandals page). "No count" means nothing fresh was received, not zero people.</p>
                    </InfoTip>
                  </caption>
                  <tbody>
                    <tr><th scope="row">Total count</th><td>{wall.totals.count ?? 'No count'}</td></tr>
                    <tr><th scope="row">% of threshold</th><td>{pct(wall.totals.pct_of_threshold)}</td></tr>
                    <tr><th scope="row">% criticality</th><td>{pct(wall.totals.pct_criticality)} <span className="lv-chip">{LEVEL_TEXT[wall.totals.level]}</span></td></tr>
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
