import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSync } from './sync';

// Loads data while the screen is focused, re-polling every `pollMs`. Keeps the last good data on errors
// so the attendant never stares at an empty screen because one request failed; with `cacheKey`, the last
// good data also survives restarts (in the SQLite cache) and `savedAt` says how old it is when it is shown.
// `key` names what is loaded (e.g. lot + API URL); when it changes, data reloads and polling restarts.
export function useResource<T>(load: () => Promise<T>, key: string, pollMs?: number, cacheKey?: string) {
  const engine = useSync();
  const cached = () => (cacheKey ? engine.saved<T>(cacheKey) : null);
  const [data, setData] = useState<T | null>(() => cached()?.data ?? null);
  const [savedAt, setSavedAt] = useState<number | null>(() => cached()?.at ?? null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const loadRef = useRef(load);
  loadRef.current = load;

  // Never show the previous lot's data under the new lot's name.
  useEffect(() => {
    const c = cached();
    setData(c?.data ?? null);
    setSavedAt(c?.at ?? null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const run = useCallback(async () => {
    try {
      const d = await loadRef.current();
      setData(d);
      setSavedAt(null);
      setError(null);
      if (cacheKey) engine.save(cacheKey, d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSavedAt((at) => at ?? (cacheKey ? engine.saved(cacheKey)?.at ?? null : null));
    }
  }, [engine, cacheKey]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    engine.kick();
    await run();
    setRefreshing(false);
  }, [engine, run]);

  useFocusEffect(
    useCallback(() => {
      run();
      if (!pollMs) return undefined;
      const id = setInterval(run, pollMs);
      return () => clearInterval(id);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [run, pollMs, key]),
  );

  return { data, setData, error, savedAt, refreshing, refresh };
}
