import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';

// Loads data while the screen is focused, re-polling every `pollMs`. Keeps the last good data on errors
// so the attendant never stares at an empty screen because one request failed.
// `key` names what is loaded (e.g. lot + API URL); when it changes, data reloads and polling restarts.
export function useResource<T>(load: () => Promise<T>, key: string, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const loadRef = useRef(load);
  loadRef.current = load;

  // Never show the previous lot's data under the new lot's name.
  useEffect(() => {
    setData(null);
    setError(null);
  }, [key]);

  const run = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await run();
    setRefreshing(false);
  }, [run]);

  useFocusEffect(
    useCallback(() => {
      run();
      if (!pollMs) return undefined;
      const id = setInterval(run, pollMs);
      return () => clearInterval(id);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [run, pollMs, key]),
  );

  return { data, setData, error, refreshing, refresh };
}
