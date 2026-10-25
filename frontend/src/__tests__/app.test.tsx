import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { AuthProvider } from '../hooks/auth';

function stubFetch(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = String(url).split('?')[0];
      if (path in routes) {
        return { ok: true, status: 200, statusText: 'OK', json: async () => routes[path] } as unknown as Response;
      }
      return { ok: false, status: 404, statusText: 'Not found', json: async () => ({ detail: 'not found' }) } as unknown as Response;
    }),
  );
}

function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const quality = { confidence: 0.9, label: 'high', reasons: [] };
const dashboard = {
  simulation: { running: false, scenario: 'normal_flow', speed: 1, minute: 0, scenarios: ['normal_flow'], source: 'simulated' },
  pandals: [
    {
      pandal: { id: 1, name: 'Demo Pandal North', location: 'North zone (demo)', capacity: 1200, warn_pct: 0.7, critical_pct: 0.9, active: 1 },
      state: { pandal_id: 1, capacity: 1200, warn_threshold: 840, critical_threshold: 1080, source: 'simulated', has_data: false, stale: false, status: 'no_data', age_s: null, quality: { confidence: 0, label: 'none', reasons: ['no measurements'] } },
      cameras: [],
      zones: [],
      active_incidents: 0,
      critical_incidents: 0,
    },
  ],
  recent_runs: [],
  llm: null,
  server_time: 0,
  data_notice: 'All measurements are simulated.',
};

function user(role: string) {
  return { user: { id: 1, username: role, role, display_name: role, pandal_ids: [] } };
}

describe('CrowdGuard frontend', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('shows the demo sign-in screen with an honest security notice when signed out', () => {
    stubFetch({});
    renderApp();
    expect(screen.getByText(/Evaluation sign-in only/i)).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Register' })).toBeTruthy();
    expect(screen.getAllByText(/Sign in/i).length).toBeGreaterThan(0);
  });

  it('renders the dashboard with the simulated-data banner and location picker for a signed-in admin', async () => {
    localStorage.setItem('cg_token', 'abc');
    stubFetch({ '/api/auth/me': user('admin'), '/api/dashboard': dashboard, '/api/pandals': [], '/api/locations': [{ id: 1, name: 'Kolkata South', active: 1, pandal_count: 0 }] });
    renderApp();
    await waitFor(() => expect(screen.getByRole('heading', { name: /Dashboard/i })).toBeTruthy());
    expect(screen.getByText(/Simulated data, not real observations/i)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Kolkata South/)).toBeTruthy());
    expect(screen.getByRole('link', { name: 'Model calls' })).toBeTruthy();
  });

  it('hides operations-only navigation for a pandal manager', async () => {
    localStorage.setItem('cg_token', 'abc');
    stubFetch({ '/api/auth/me': user('manager'), '/api/dashboard': dashboard, '/api/pandals': [], '/api/locations': [] });
    renderApp();
    await waitFor(() => expect(screen.getByRole('heading', { name: /Dashboard/i })).toBeTruthy());
    expect(screen.queryByRole('link', { name: 'Model calls' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'System health' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Incidents' })).toBeTruthy();
  });

  it('blocks a direct visit to an operations page for a manager', async () => {
    localStorage.setItem('cg_token', 'abc');
    stubFetch({ '/api/auth/me': user('manager') });
    renderApp('/llm');
    await waitFor(() => expect(screen.getByText(/does not have access/i)).toBeTruthy());
  });
});
