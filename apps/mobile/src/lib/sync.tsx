import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { AppState, Text, View } from 'react-native';
import { MemoryStore, type Store } from '../core/store';
import { SyncEngine } from '../core/sync';
import { t } from '../strings';
import { c } from '../theme';
import { uuidv7 } from './ids';
import { useApp } from './settings';
import { SqliteStore } from './sqlite-store';

const Ctx = createContext<SyncEngine | null>(null);

async function openStore(): Promise<Store> {
  try {
    return await SqliteStore.open();
  } catch (e) {
    // e.g. web without the wasm setup: keep working, just without surviving a restart.
    console.warn('SQLite unavailable, cache kept in memory only', e);
    return new MemoryStore();
  }
}

// Owns the one SyncEngine (cache + outbox) for the app and keeps it pointed at the current API URL and lot.
export function SyncProvider({ children }: { children: ReactNode }) {
  const { ready, settings, api } = useApp();
  const [engine, setEngine] = useState<SyncEngine | null>(null);

  useEffect(() => {
    if (!ready) return undefined;
    let cancelled = false;
    let created: SyncEngine | null = null;
    (async () => {
      const e = new SyncEngine({ store: await openStore(), api, lotId: settings.lotId, newId: uuidv7,
        log: __DEV__ ? (m) => console.log(`[sync] ${m}`) : undefined });
      await e.init();
      if (cancelled) return;
      created = e;
      e.start();
      setEngine(e);
    })();
    return () => {
      cancelled = true;
      created?.stop();
    };
    // Created once; the effects below follow API URL and lot changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  useEffect(() => engine?.setApi(api), [engine, api]);
  useEffect(() => engine?.setLot(settings.lotId), [engine, settings.lotId]);

  // Back in the foreground (e.g. leaving airplane mode settings): sync now instead of at the next tick.
  useEffect(() => {
    if (!engine) return undefined;
    const sub = AppState.addEventListener('change', (state) => state === 'active' && engine.kick());
    return () => sub.remove();
  }, [engine]);

  if (!engine) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: c.bg }}>
        <Text style={{ color: c.muted }}>{t.common.loading}</Text>
      </View>
    );
  }
  return <Ctx.Provider value={engine}>{children}</Ctx.Provider>;
}

// The sync engine; the calling component re-renders whenever the cache, outbox or status changes.
export function useSync(): SyncEngine {
  const engine = useContext(Ctx);
  if (!engine) throw new Error('useSync must be used inside SyncProvider');
  useSyncExternalStore((fn) => engine.subscribe(fn), () => engine.version);
  return engine;
}
