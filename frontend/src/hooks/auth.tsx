import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, tokenStore } from '../api/client';
import type { Role, User } from '../types';

// Mirrors backend/security/auth.py PERMISSIONS. This only decides what the UI SHOWS.
// The backend enforces every permission again on each request.
const PERMS: Record<string, Role[]> = {
  read: ['admin', 'operator', 'manager', 'auditor'],
  'registry:write': ['admin'],
  'sim:control': ['admin', 'operator'],
  'incident:write': ['admin', 'operator', 'manager'],
  'ops:read': ['admin', 'operator', 'auditor'],
  'settings:write': ['admin'],
  'audit:read': ['admin', 'auditor'],
};

export const ROLE_LABELS: Record<Role, string> = {
  admin: 'Platform administrator',
  operator: 'Control-room operator',
  manager: 'Pandal manager',
  auditor: 'Read-only auditor',
};

interface AuthValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, displayName?: string) => Promise<string>;
  logout: () => Promise<void>;
  can: (perm: string) => boolean;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(!!tokenStore.get());

  useEffect(() => {
    if (!tokenStore.get()) return;
    api
      .me()
      .then((r) => setUser(r.user))
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onUnauthorized = () => {
      tokenStore.clear();
      setUser(null);
    };
    window.addEventListener('cg-unauthorized', onUnauthorized);
    return () => window.removeEventListener('cg-unauthorized', onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    tokenStore.set(r.token);
    setUser(r.user);
  }, []);

  const register = useCallback(async (username: string, password: string, displayName?: string) => {
    const r = await api.register(username, password, displayName);
    return r.note;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* session may already be gone */
    }
    tokenStore.clear();
    setUser(null);
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      user,
      loading,
      login,
      register,
      logout,
      can: (perm: string) => !!user && (PERMS[perm] ?? []).includes(user.role),
    }),
    [user, loading, login, register, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth must be used inside AuthProvider');
  return v;
}
