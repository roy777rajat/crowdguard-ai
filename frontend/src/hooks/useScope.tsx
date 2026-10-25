import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Location, Pandal } from '../types';
import { usePolling } from './usePolling';

const LOC_KEY = 'cg_scope_location';
const PANDAL_KEY = 'cg_scope_pandal';

function readNum(key: string): number | null {
  try {
    const v = localStorage.getItem(key);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

function writeNum(key: string, v: number | null): void {
  try {
    if (v === null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(v));
  } catch {
    /* storage unavailable: the choice just is not remembered */
  }
}

/** Location first, then pandal. The choice is shared by the Dashboard and the Camera wall. */
export function useScope() {
  const locs = usePolling(() => api.locations(), 0);
  const [locationId, setLoc] = useState<number | null>(() => readNum(LOC_KEY));
  const [pandalId, setPan] = useState<number | null>(() => readNum(PANDAL_KEY));
  const pandals = usePolling(
    () => (locationId === null ? Promise.resolve([] as Pandal[]) : api.pandalsIn(locationId)),
    0,
    [locationId],
  );

  useEffect(() => {
    const list: Location[] = locs.data ?? [];
    if (list.length === 0) return;
    if (locationId === null || !list.some((l) => l.id === locationId)) {
      const first = list.find((l) => l.pandal_count > 0) ?? list[0];
      setLoc(first.id);
      writeNum(LOC_KEY, first.id);
    }
  }, [locs.data, locationId]);

  useEffect(() => {
    if (!pandals.data || locationId === null) return;
    if (pandalId === null || !pandals.data.some((p) => p.id === pandalId)) {
      const first = pandals.data[0]?.id ?? null;
      setPan(first);
      writeNum(PANDAL_KEY, first);
    }
  }, [pandals.data, pandalId, locationId]);

  return {
    locations: locs.data ?? [],
    pandals: pandals.data ?? [],
    locationId,
    pandalId,
    pandal: (pandals.data ?? []).find((p) => p.id === pandalId) ?? null,
    loading: locs.loading,
    error: locs.error,
    setLocation: (id: number) => {
      setLoc(id);
      writeNum(LOC_KEY, id);
      setPan(null);
      writeNum(PANDAL_KEY, null);
    },
    setPandal: (id: number) => {
      setPan(id);
      writeNum(PANDAL_KEY, id);
    },
  };
}

export type Scope = ReturnType<typeof useScope>;
