import { useState, type FormEvent } from 'react';
import { Notice } from '../components/States';
import { useAuth } from '../hooks/auth';
import '../styles/login.css';

const DEMO_USERS = [
  { username: 'admin', password: 'demo-admin', text: 'Administrator' },
  { username: 'operator', password: 'demo-operator', text: 'Control-room operator' },
  { username: 'manager', password: 'demo-manager', text: 'Pandal manager' },
  { username: 'auditor', password: 'demo-auditor', text: 'Read-only auditor' },
];

/** Logo: a shield holding a lotus (the Puja flower) over a small crowd of people. Pure SVG, no external image. */
export function Logo({ size = 64 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="CrowdGuard AI logo">
      <defs>
        <linearGradient id="lg-shield" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ff6a3d" />
          <stop offset="1" stopColor="#b3122b" />
        </linearGradient>
        <linearGradient id="lg-gold" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffe08a" />
          <stop offset="1" stopColor="#f0a419" />
        </linearGradient>
      </defs>
      <path d="M32 3 8 11v18c0 15 10 27 24 32 14-5 24-17 24-32V11L32 3Z" fill="url(#lg-shield)" stroke="#ffe08a" strokeWidth="2" />
      <g fill="url(#lg-gold)">
        <path d="M32 14c4 5 6 9 6 13a6 6 0 0 1-12 0c0-4 2-8 6-13Z" />
        <path d="M21 20c5 1 8 4 10 8-2 3-5 4-8 3-4-1-5-6-2-11Z" opacity=".9" />
        <path d="M43 20c-5 1-8 4-10 8 2 3 5 4 8 3 4-1 5-6 2-11Z" opacity=".9" />
      </g>
      <g fill="#fff3d6">
        <circle cx="20" cy="42" r="3" /><circle cx="32" cy="40" r="3.4" /><circle cx="44" cy="42" r="3" />
        <path d="M15 53c0-5 2.5-8 5-8s5 3 5 8Z" /><path d="M26.5 52c0-5 2.5-8 5.5-8s5.500 3 5.500 8Z" /><path d="M39 53c0-5 2.500-8 5-8s5 3 5 8Z" />
      </g>
    </svg>
  );
}

/** Slowly turning mandala with ten arms (a nod to Maa Durga's ten hands) over a warm glow. Decorative only. */
export function Backdrop() {
  const arms = Array.from({ length: 10 }, (_, i) => i * 36);
  return (
    <div className="lg-bg" aria-hidden="true">
      <div className="lg-glow" />
      <svg className="lg-mandala" viewBox="-200 -200 400 400">
        <g fill="none" stroke="currentColor" strokeWidth="1.2">
          {[190, 150, 112, 70].map((r) => <circle key={r} r={r} strokeDasharray={r % 2 ? '2 6' : undefined} />)}
          {arms.map((a) => (
            <g key={a} transform={`rotate(${a})`}>
              <path d="M0 -70 C 22 -100, 22 -140, 0 -190 C -22 -140, -22 -100, 0 -70Z" />
              <path d="M0 -70 C 8 -95, 8 -125, 0 -160 C -8 -125, -8 -95, 0 -70Z" opacity=".6" />
            </g>
          ))}
        </g>
        <circle r="26" fill="currentColor" opacity=".25" />
      </svg>
      {Array.from({ length: 14 }, (_, i) => (
        <span key={i} className="lg-petal" style={{ left: `${(i * 7.3) % 100}%`, animationDelay: `${-i * 1.7}s`, animationDuration: `${12 + (i % 5) * 2}s` }} />
      ))}
      <div className="lg-watermark">শুভ শারদীয়া</div>
    </div>
  );
}

export default function Login() {
  const { login, register } = useAuth();
  const [tab, setTab] = useState<'signin' | 'register'>('signin');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      if (tab === 'signin') {
        await login(username, password);
      } else {
        const note = await register(username, password, displayName || undefined);
        setInfo(`Account created. ${note} You can now sign in.`);
        setTab('signin');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lg-wrap">
      <Backdrop />
      <main className="lg-card">
        <div className="lg-brand">
          <Logo size={72} />
          <h1>CrowdGuard <span>AI</span></h1>
          <p>Safer Pujo, calmer crowds</p>
        </div>
        <div className="lg-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'signin'} onClick={() => setTab('signin')}>Sign in</button>
          <button role="tab" aria-selected={tab === 'register'} onClick={() => setTab('register')}>Register</button>
        </div>
        <form onSubmit={(e) => void submit(e)}>
          <label>
            Username
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </label>
          {tab === 'register' && (
            <label>
              Display name (optional)
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </label>
          )}
          <label>
            Password {tab === 'register' && '(at least 8 characters)'}
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete={tab === 'signin' ? 'current-password' : 'new-password'} required />
          </label>
          {error && <Notice kind="error">{error}</Notice>}
          {info && <Notice kind="ok">{info}</Notice>}
          <button className="lg-submit" type="submit" disabled={busy}>{tab === 'signin' ? 'Sign in' : 'Create account'}</button>
          {tab === 'register' && <p className="lg-small">New accounts get the read-only auditor role. An administrator assigns roles.</p>}
        </form>
        {tab === 'signin' && (
          <div className="lg-demo">
            <span>Quick sign-in</span>
            <div>
              {DEMO_USERS.map((u) => (
                <button key={u.username} type="button" onClick={() => { setUsername(u.username); setPassword(u.password); }}>
                  {u.text}
                </button>
              ))}
            </div>
          </div>
        )}
        <p className="lg-small lg-note">Evaluation sign-in only. Not production security: no email verification, MFA or rate limiting. Data shown is simulated.</p>
      </main>
    </div>
  );
}
