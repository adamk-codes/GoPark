import type { Lot, LotOverview, ScanResult } from './contract';

// Set EXPO_PUBLIC_API_URL in apps/mobile/.env to the laptop's LAN address, e.g. http://192.168.1.20:8000
export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

// TODO(P1): replace with a UUIDv7 library once the offline outbox lands.
export function eventId(): string {
  const hex = () => Math.floor(Math.random() * 16).toString(16);
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) =>
    c === 'x' ? hex() : ((Math.random() * 4) | 8).toString(16),
  );
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  lots: () => call<Lot[]>('/lots'),
  overview: (lot: string) => call<LotOverview>(`/lots/${lot}/overview`),
  scan: (body: { lot_id: string; device_id: string; booking_id?: string; plate?: string }) =>
    call<ScanResult>('/scan', {
      method: 'POST',
      body: JSON.stringify({ ...body, client_event_id: eventId(), observed_at: new Date().toISOString() }),
    }),
};
