import { useEffect, useState, type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { ROLE_LABELS, useAuth } from '../hooks/auth';
import { Backdrop, Logo } from '../pages/Login';
import '../styles/theme-durga.css';

const NAV: Array<{ to: string; text: string; perm: string }> = [
  { to: '/', text: 'Dashboard', perm: 'read' },
  { to: '/live', text: 'Live monitoring', perm: 'read' },
  { to: '/camera-wall', text: 'Camera wall', perm: 'read' },
  { to: '/pandals', text: 'Pandals', perm: 'read' },
  { to: '/cameras', text: 'Cameras', perm: 'read' },
  { to: '/incidents', text: 'Incidents', perm: 'read' },
  { to: '/runs', text: 'Workflow runs', perm: 'read' },
  { to: '/llm', text: 'Model calls', perm: 'ops:read' },
  { to: '/system', text: 'System health', perm: 'ops:read' },
  { to: '/settings', text: 'Settings and audit', perm: 'ops:read' },
];

type Theme = 'light' | 'dark';

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('cg_theme');
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    /* storage unavailable */
  }
  return 'dark'; // the Durga theme matches the sign-in page; light is one click away
}

export default function Layout({ children }: { children: ReactNode }) {
  const { user, can, logout } = useAuth();
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('cg_theme', theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  return (
    <>
    <div className="app-bg"><Backdrop /></div>
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Logo size={30} />
          <span className="brand-name">CrowdGuard</span>
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.filter((n) => can(n.perm)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>
              {n.text}
            </NavLink>
          ))}
        </nav>
      </aside>
      <header className="topbar">
        <div className="notice" role="note">
          Simulated data, not real observations. Sign-in here is not production security.
        </div>
        <div className="userbox">
          <button className="btn small" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? 'Light theme' : 'Dark theme'}
          </button>
          <span>
            {user?.display_name ?? user?.username} <span className="sub">({user ? ROLE_LABELS[user.role] : ''})</span>
          </span>
          <button className="btn small" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </header>
      <main className="main">{children}</main>
    </div>
    </>
  );
}
