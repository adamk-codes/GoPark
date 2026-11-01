import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ConfirmAnswer, PendingConfirmation } from '../api';
import { clockFrom, demoNowMs, PHONE_CLOCK } from '../core/clock';
import type { OutboxState } from '../core/requests';
import { driverOutbox, flushDriver, loadDriver, queueConfirm, saveDriver, type DriverConfig } from './driver';
import { useApp } from './settings';

const POLL_MS = 15_000;
const PROMPTS_KEY = 'gopark.driver.prompts.v1'; // last confirmations fetched + those answered on this phone

interface Prompts {
  items: PendingConfirmation[];
  answered: string[]; // `${stay_id}|${question}`
  mocked: boolean;
}

export const promptKey = (p: PendingConfirmation) => `${p.stay_id}|${p.question}`;

// Driver screens' state: config mirrored for the background task, the confirm prompts and the outbox.
// Answers are queued before they are sent, so a tap never waits on the network.
export function useDriver() {
  const { api, settings } = useApp();
  const [cfg, setCfg] = useState<DriverConfig | null>(null);
  const [prompts, setPrompts] = useState<Prompts>({ items: [], answered: [], mocked: false });
  const [outbox, setOutbox] = useState<OutboxState>({ pending: [], failed: [], online: null });
  const [error, setError] = useState<string | null>(null);
  const cfgRef = useRef<DriverConfig | null>(null);
  cfgRef.current = cfg;

  const base = useCallback((): DriverConfig => ({ apiUrl: settings.apiUrl, deviceId: settings.deviceId,
    userId: settings.driverUserId, devMocks: settings.devMocks, clock: PHONE_CLOCK, bookings: [], lots: [],
    sensing: false }), [settings.apiUrl, settings.deviceId, settings.driverUserId, settings.devMocks]);

  const update = useCallback(async (patch: Partial<DriverConfig>) => {
    const next = await saveDriver(patch, cfgRef.current ?? base());
    setCfg(next);
    return next;
  }, [base]);

  // Settings are the source of truth for URL, device and user; copy them where the background task can read.
  useEffect(() => {
    (async () => {
      const saved = await loadDriver();
      const raw = await AsyncStorage.getItem(PROMPTS_KEY).catch(() => null);
      if (raw) setPrompts(JSON.parse(raw));
      setOutbox(await driverOutbox.state());
      setCfg(await saveDriver({ apiUrl: settings.apiUrl, deviceId: settings.deviceId, userId: settings.driverUserId,
        devMocks: settings.devMocks }, saved ?? base()));
    })().catch((e) => setError(String(e)));
  }, [settings.apiUrl, settings.deviceId, settings.driverUserId, settings.devMocks, base]);

  const savePrompts = useCallback((p: Prompts) => {
    setPrompts(p);
    AsyncStorage.setItem(PROMPTS_KEY, JSON.stringify(p)).catch(() => undefined);
  }, []);

  // One refresh: drain the outbox, learn the demo clock and lots, fetch what the server wants confirmed.
  const refresh = useCallback(async () => {
    const c = cfgRef.current;
    if (!c) return;
    setOutbox(await flushDriver(c));
    try {
      const [health, lots] = await Promise.all([api.health(), api.lots()]);
      await update({ clock: clockFrom(health.now), lots: lots.map((l) => ({ id: l.id, name: l.name,
        lat: l.center_lat, lng: l.center_lng, radius_m: l.radius_m })) });
      const res = await api.pendingConfirmations(c.userId);
      // Forget local answers the server no longer asks about; keep hiding the ones it still lists until synced.
      const keys = new Set(res.data.map(promptKey));
      setPrompts((prev) => {
        const next = { items: res.data, answered: prev.answered.filter((k) => keys.has(k)), mocked: res.mocked };
        AsyncStorage.setItem(PROMPTS_KEY, JSON.stringify(next)).catch(() => undefined);
        return next;
      });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); // offline: keep the last prompts on screen
    }
  }, [api, update]);

  useFocusEffect(useCallback(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]));

  useEffect(() => {
    if (cfg) refresh();
    // First refresh once the config exists (focus may have fired before).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg === null]);

  const answer = useCallback(async (p: PendingConfirmation, a: ConfirmAnswer | null, label: string) => {
    const c = cfgRef.current;
    if (!c) return;
    savePrompts({ ...prompts, answered: [...prompts.answered, promptKey(p)] }); // hide at once
    if (!a) return; // "Still parked": nothing to tell the server
    setOutbox(await queueConfirm(c, p.stay_id, a, label));
  }, [prompts, savePrompts]);

  const dismissFailed = useCallback(async (id: string) => {
    await driverOutbox.dismissFailed(id);
    setOutbox(await driverOutbox.state());
  }, []);

  const open = prompts.items.filter((p) => !prompts.answered.includes(promptKey(p)));
  const nowMs = demoNowMs(cfg?.clock ?? PHONE_CLOCK);
  return { cfg, update, prompts: open, promptsMocked: prompts.mocked, outbox, error, refresh, answer, dismissFailed,
    nowMs };
}
