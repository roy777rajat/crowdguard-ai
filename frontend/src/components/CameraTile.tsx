import { useEffect, useRef, useState, type MutableRefObject, type RefObject } from 'react';
import { api } from '../api/client';
import InfoTip from './InfoTip';
import type { CrowdLevel, VisionResult, WallZone } from '../types';

export type RunState = 'stopped' | 'running' | 'paused';

export const FRAME_W = 640; // frames are sent at this width; the height follows the video's own shape
const FRAME_H = 360;

export const LEVEL_TEXT: Record<CrowdLevel, string> = {
  quiet: 'Quiet', busy: 'Busy', crowded: 'Crowded', unknown: 'No count', passive: 'Passive',
};
export const LEVEL_COLOR: Record<CrowdLevel, string> = {
  quiet: 'var(--lv-quiet)', busy: 'var(--lv-busy)', crowded: 'var(--lv-crowded)', unknown: 'var(--lv-unknown)', passive: 'var(--lv-unknown)',
};

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Synthetic crowd animation. Drawn by this page, not recorded. Freezes when `running` is false. */
function useDemoCrowd(canvasRef: RefObject<HTMLCanvasElement>, seed: number, entering: boolean,
  truth: MutableRefObject<number>, running: MutableRefObject<boolean>, resetToken: number) {
  useEffect(() => {
    const canvas = canvasRef.current;
    const g = canvas?.getContext('2d');
    if (!canvas || !g) return undefined;
    const rnd = rng(seed * 7919 + 13);
    const colors = ['#e4572e', '#29335c', '#f3a712', '#a8c686', '#669bbc', '#8f2d56'];
    type P = { x: number; y: number; v: number; s: number; c: string };
    let people: P[] = [];
    let t = rnd() * 50;
    let last = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (running.current) {
        t += dt;
        const target = 6 + 26 * (0.5 + 0.5 * Math.sin(t / 14 + seed));
        if (people.length < target && rnd() < dt * 5) {
          const y = 150 + rnd() * 190;
          people.push({ x: entering ? -20 : FRAME_W + 20, y, v: (30 + rnd() * 45) * (entering ? 1 : -1), s: 0.55 + ((y - 150) / 190) * 0.8, c: colors[Math.floor(rnd() * colors.length)] });
        }
        people.forEach((p) => { p.x += p.v * dt; });
        people = people.filter((p) => p.x > -40 && p.x < FRAME_W + 40);
        g.fillStyle = '#2b2f36';
        g.fillRect(0, 0, FRAME_W, FRAME_H);
        g.fillStyle = '#3a3f48';
        g.fillRect(0, 130, FRAME_W, FRAME_H - 130);
        g.fillStyle = '#6b7280';
        g.fillRect(entering ? FRAME_W - 22 : 0, 120, 22, 230);
        [...people].sort((a, b) => a.y - b.y).forEach((p) => {
          g.fillStyle = p.c;
          g.fillRect(p.x - 6 * p.s, p.y - 26 * p.s, 12 * p.s, 26 * p.s);
          g.fillStyle = '#f1d3b3';
          g.beginPath();
          g.arc(p.x, p.y - 32 * p.s, 6 * p.s, 0, Math.PI * 2);
          g.fill();
        });
        g.fillStyle = 'rgba(255,255,255,0.85)';
        g.font = '14px sans-serif';
        g.fillText('SYNTHETIC TEST ANIMATION (not a real camera)', 10, 22);
        truth.current = people.filter((p) => p.x > 0 && p.x < FRAME_W).length;
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [canvasRef, seed, entering, truth, running, resetToken]);
}

interface Props {
  zoneWall: WallZone;
  run: RunState;
  resetToken: number;
  everyMs: number;
  canAnalyze: boolean;
  canRegister: boolean;
  realModel: boolean;
  gallery: string[];
  onChanged: () => void;
}

export default function CameraTile({ zoneWall, run, resetToken, everyMs, canAnalyze, canRegister, realModel, gallery, onChanged }: Props) {
  const cam = zoneWall.camera;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const truth = useRef(0);
  const runningRef = useRef(false);
  const busy = useRef(false);
  const [override, setOverride] = useState<string | null>(null); // '__demo' or a gallery file name, shown before it is saved
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [feedError, setFeedError] = useState<string | null>(null);
  const [result, setResult] = useState<VisionResult | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const passive = (cam?.mode ?? 'active') === 'passive';
  const registered = !cam ? '__demo' : cam.source_type === 'simulated' ? '__demo' : cam.source_type === 'local_video' ? (cam.source_ref ?? '__demo') : '__stream';
  const selected = override ?? registered;
  const demo = selected === '__demo';
  const stream = selected === '__stream';
  const demoCountable = demo && cam?.source_type === 'simulated';
  runningRef.current = run === 'running';

  useDemoCrowd(canvasRef, cam?.id ?? 0, (cam?.id ?? 0) % 2 === 1, truth, runningRef, resetToken);

  // Load the chosen gallery video (as a blob, because the endpoint needs the sign-in token).
  useEffect(() => {
    setVideoUrl(null);
    setFeedError(null);
    setResult(null);
    setAnalyzeError(null);
    if (stream && cam?.source_ref) { setVideoUrl(cam.source_ref); return undefined; }
    if (demo) return undefined;
    let url: string | null = null;
    let alive = true;
    api.visionVideoUrl(selected).then(
      (u) => { url = u; if (alive) setVideoUrl(u); else URL.revokeObjectURL(u); },
      (e) => alive && setFeedError(e instanceof Error ? `${e.message}.` : 'Video failed to load'),
    );
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [selected, demo, stream, cam?.source_ref]);

  // Start / pause / stop the video together with the wall controls.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (run === 'running') void v.play().catch(() => undefined);
    else if (run === 'paused') v.pause();
    else { v.pause(); v.currentTime = 0; }
  }, [run, videoUrl]);

  useEffect(() => {
    const v = videoRef.current;
    if (v) v.currentTime = 0;
    setResult(null);
  }, [resetToken]);

  // Send one frame per interval while running.
  useEffect(() => {
    if (!cam || !canAnalyze || run !== 'running' || passive) return undefined;
    const id = window.setInterval(() => {
      if (busy.current || document.hidden) return;
      if (demo && !demoCountable) return; // animation shown on a camera registered as a video: nothing real to count
      const video = videoRef.current;
      const src: CanvasImageSource | null = demo ? canvasRef.current : video;
      if (!src || (!demo && (!video || video.readyState < 2 || video.paused))) return;
      const ratio = !demo && video && video.videoWidth > 0 ? video.videoHeight / video.videoWidth : FRAME_H / FRAME_W;
      const off = document.createElement('canvas');
      off.width = FRAME_W;
      off.height = Math.max(16, Math.round(FRAME_W * ratio));
      const g = off.getContext('2d');
      if (!g) return;
      try {
        g.drawImage(src, 0, 0, off.width, off.height);
      } catch {
        setAnalyzeError('The browser blocked reading this stream (needs CORS). Use a gallery video.');
        return;
      }
      busy.current = true;
      off.toBlob((blob) => {
        if (!blob) { busy.current = false; return; }
        api.visionAnalyze(cam.id, blob, demoCountable ? truth.current : undefined)
          .then((r) => { setResult(r); setAnalyzeError(null); })
          .catch((e) => setAnalyzeError(e instanceof Error ? e.message : 'Analysis failed'))
          .finally(() => { busy.current = false; });
      }, 'image/jpeg', 0.7);
    }, everyMs);
    return () => window.clearInterval(id);
    // Depend on the camera id only: the wall refreshes `cam` every few seconds, and re-creating the
    // timer on each refresh meant a 10 s snapshot interval never got to fire.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cam?.id, canAnalyze, run, passive, demo, demoCountable, everyMs]);

  async function choose(value: string) {
    if (!cam) return;
    setMsg(null);
    setOverride(value);
    if (!canRegister) { setMsg('Shown on this screen only (your role cannot save camera settings).'); return; }
    try {
      await api.updateCamera(cam.id, value === '__demo' ? { source_type: 'simulated' } : { source_type: 'local_video', source_ref: value });
      setOverride(null);
      onChanged();
    } catch (e) {
      setMsg(`Shown here only; could not save (${e instanceof Error ? e.message : 'error'}).`);
    }
  }

  async function setMode(mode: 'active' | 'passive') {
    if (!cam) return;
    setMsg(null);
    try {
      await api.updateCamera(cam.id, { mode });
      onChanged();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Could not change the camera mode');
    }
  }

  const level: CrowdLevel = zoneWall.level;
  const color = LEVEL_COLOR[level];
  const count = zoneWall.count;
  const note = analyzeError ?? (result && result.count === null ? result.note : null)
    ?? (zoneWall.stale ? 'Last count is old; waiting for a fresh one.' : null) ?? zoneWall.last_error;

  return (
    <section className="tile" style={{ ['--lv' as string]: color }} aria-label={`${zoneWall.zone.name}: ${LEVEL_TEXT[level]}`}>
      <div className="tile-head">
        <span>{zoneWall.zone.name}
          <InfoTip title={`Zone: ${zoneWall.zone.name}`}>
            <p>One of the pandal's 3 zones, watched by one camera. Pick a video from the gallery folder (or the test animation) in the dropdown.</p>
            <p><b>Active</b> cameras are counted and can raise warnings. <b>Passive</b> cameras are only displayed. The test animation is never sent to Meta and never billed.</p>
          </InfoTip>
        </span>
        <span className="sub">{passive ? 'Passive' : 'Active'}</span>
      </div>
      {cam && canAnalyze && (
        <div className="tile-src">
          <label>
            Video{' '}
            <select value={selected} onChange={(e) => void choose(e.target.value)} aria-label={`Video for ${zoneWall.zone.name}`}>
              <option value="__demo">Test animation</option>
              {gallery.map((f) => <option key={f} value={f}>{f}</option>)}
              {stream && <option value="__stream">Live stream (registered)</option>}
              {!demo && !stream && !gallery.includes(selected) && <option value={selected}>{selected} (missing)</option>}
            </select>
          </label>
          {canRegister && (
            <label>
              Mode{' '}
              <select value={passive ? 'passive' : 'active'} onChange={(e) => void setMode(e.target.value as 'active' | 'passive')}
                aria-label={`Camera mode for ${zoneWall.zone.name}`}>
                <option value="active">Active (counted)</option>
                <option value="passive">Passive (shown only)</option>
              </select>
            </label>
          )}
        </div>
      )}
      <div className="tile-feed">
        <canvas ref={canvasRef} width={FRAME_W} height={FRAME_H} style={demo ? undefined : { display: 'none' }} />
        {!demo && videoUrl && (
          <video ref={videoRef} src={videoUrl} loop muted playsInline preload="auto" crossOrigin={stream ? 'anonymous' : undefined}
            onError={() => setFeedError('This video could not be played.')} />
        )}
        {!demo && !videoUrl && !feedError && <div className="msg">Loading video…</div>}
        {feedError && <div className="msg" role="alert">{feedError}</div>}
        {!cam && <div className="msg">No camera in this zone.</div>}
        {passive && <div className="badge-passive">Passive: not counted</div>}
        {run !== 'running' && cam && <div className="badge-state">{run === 'paused' ? 'Paused' : 'Stopped'}</div>}
        {!passive && result && result.boxes.length > 0 && (
          <svg viewBox={`0 0 ${result.frame_w ?? FRAME_W} ${result.frame_h ?? FRAME_H}`} aria-hidden="true">
            {result.boxes.map((b) => (
              <g key={b.id}>
                <rect x={b.box[0]} y={b.box[1]} width={Math.max(0, b.box[2] - b.box[0])} height={Math.max(0, b.box[3] - b.box[1])}
                  fill="none" stroke="#00e5ff" strokeWidth="2" vectorEffect="non-scaling-stroke" />
                <text x={b.box[0] + 3} y={Math.max(12, b.box[1] - 3)} fill="#00e5ff" fontSize="12">
                  {b.id}{b.score != null ? ` ${Math.round(b.score * 100)}%` : ''}
                </text>
              </g>
            ))}
          </svg>
        )}
      </div>
      <div className="tile-foot">
        <div className="tile-count" aria-live="polite">
          {count === null ? '—' : count}<small>{count === 1 ? 'person' : 'people'} in view</small>
        </div>
        <span className="lv-chip">{LEVEL_TEXT[level]}</span>
        <span className="age">{zoneWall.age_s == null ? 'no data yet' : `${Math.round(zoneWall.age_s)}s ago`}</span>
      </div>
      {(note || msg || (!demo && !realModel)) && (
        <div className="note">
          {!demo && !realModel ? 'Simulated detector: enable SAM 3.1 to count this video. ' : ''}{note ?? ''} {msg ?? ''}
        </div>
      )}
    </section>
  );
}
