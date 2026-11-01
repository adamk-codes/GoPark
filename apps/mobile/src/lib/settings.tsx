import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createApi, DEFAULT_API_URL, type Api } from '../api';
import { newDeviceId } from './ids';

export interface Settings {
  apiUrl: string;
  lotId: string;
  deviceId: string;
  driverUserId: string;
}

const KEY = 'gopark.settings.v1';

interface AppCtx {
  ready: boolean;
  settings: Settings;
  update: (patch: Partial<Settings>) => Promise<void>;
  api: Api;
}

const Ctx = createContext<AppCtx | null>(null);

function defaults(): Settings {
  return { apiUrl: DEFAULT_API_URL, lotId: 'AUB', deviceId: newDeviceId(),
    driverUserId: 'U0001' };
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(defaults);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(KEY);
        if (raw) {
          // Mock flags from earlier phases (mockPending, devMocks) no longer exist: drop them.
          const { mockPending: _p1, devMocks: _p2, ...saved } = JSON.parse(raw);
          setSettings((s) => ({ ...s, ...saved }));
        } else {
          // First launch: keep the generated device ID for good.
          await AsyncStorage.setItem(KEY, JSON.stringify(latest.current));
        }
      } catch {
        // Storage failure: run with defaults rather than block the attendant.
      }
      setReady(true);
    })();
  }, []);

  const latest = useRef(settings);
  latest.current = settings;
  const update = useCallback(async (patch: Partial<Settings>) => {
    const next = { ...latest.current, ...patch };
    latest.current = next;
    setSettings(next);
    await AsyncStorage.setItem(KEY, JSON.stringify(next)).catch(() => undefined);
  }, []);

  const api = useMemo(
    () => createApi({ baseUrl: settings.apiUrl, deviceId: settings.deviceId }),
    [settings.apiUrl, settings.deviceId],
  );

  const value = useMemo(() => ({ ready, settings, update, api }), [ready, settings, update, api]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp must be used inside SettingsProvider');
  return v;
}
