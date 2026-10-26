import type { ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { Loading } from './components/States';
import { useAuth } from './hooks/auth';
import Assistant from './pages/Assistant';
import CameraWall from './pages/CameraWall';
import Cameras from './pages/Cameras';
import Dashboard from './pages/Dashboard';
import IncidentDetail from './pages/IncidentDetail';
import Incidents from './pages/Incidents';
import Live from './pages/Live';
import Llm from './pages/Llm';
import Login from './pages/Login';
import Pandals from './pages/Pandals';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';
import SettingsPage from './pages/SettingsPage';
import System from './pages/System';

/** UI convenience only. The backend rejects unauthorized requests regardless of what the UI shows. */
function Guard({ perm, children }: { perm: string; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(perm)) {
    return (
      <div className="state-box" role="alert">
        Your role does not have access to this page.
      </div>
    );
  }
  return <>{children}</>;
}

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading label="Checking your session" />;
  if (!user) return <Login />;

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/assistant" element={<Assistant />} />
        <Route path="/live" element={<Live />} />
        <Route path="/pandals" element={<Pandals />} />
        <Route path="/camera-wall" element={<CameraWall />} />
        <Route path="/cameras" element={<Cameras />} />
        <Route path="/incidents" element={<Incidents />} />
        <Route path="/incidents/:id" element={<IncidentDetail />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<RunDetail />} />
        <Route path="/llm" element={<Guard perm="ops:read"><Llm /></Guard>} />
        <Route path="/system" element={<Guard perm="ops:read"><System /></Guard>} />
        <Route path="/settings" element={<Guard perm="ops:read"><SettingsPage /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
