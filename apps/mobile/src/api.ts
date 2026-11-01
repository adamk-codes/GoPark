import type {
  Alert, AuditEntry, Booking, BookingQr, Lot, LotOverview, QrPublicKey, ScanResult, Stay, SyncEvent,
} from './contract';

// Default server; Settings can override it. Put EXPO_PUBLIC_API_URL=http://<laptop-LAN-IP>:8000 in apps/mobile/.env.
export const DEFAULT_API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
// Dev-only: EXPO_PUBLIC_DEV_MOCKS=1 lets endpoints that are still stubs (404/405/501) answer with a mock.
export const DEFAULT_DEV_MOCKS = process.env.EXPO_PUBLIC_DEV_MOCKS === '1';

// Shapes the API returns that contract/types.ts doesn't list yet (from api/ops/routes.py on main).
// TODO(P2): move these into contract/types.ts.
export interface Health {
  ok: boolean;
  now: string;
  version: string;
}

export interface Rewards {
  user_id: string;
  points: number;
  grace_minutes: number;
  tier: string;
}

export interface SyncPull {
  cursor: string;
  bookings: Booking[];
  stays: Stay[];
  alerts: Alert[];
}

export interface SyncPushResult {
  acks: string[];
  conflicts: { id: string; reason: string }[];
  results?: Record<string, ScanResult | Alert>;
}

export interface ApiConfig {
  baseUrl: string;
  deviceId: string;
  devMocks: boolean;
  timeoutMs?: number;
}

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`${status}: ${body}`);
    this.status = status;
    this.body = body;
  }
}

// A response from an endpoint that may be mocked in dev: `mocked` tells the UI to say so.
export interface Maybe<T> {
  data: T;
  mocked: boolean;
}

export type ScanInput = { qr: string } | { booking_id: string } | { plate: string };

// Status codes that mean "this endpoint is still a stub".
const NOT_BUILT = new Set([404, 405, 501]);

export function createApi(cfg: ApiConfig) {
  const base = cfg.baseUrl.replace(/\/+$/, '');

  async function call<T>(path: string, init?: RequestInit): Promise<T> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), cfg.timeoutMs ?? 8000);
    try {
      const res = await fetch(`${base}${path}`, {
        ...init,
        signal: ctrl.signal,
        headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      });
      if (!res.ok) throw new ApiError(res.status, await res.text());
      return (await res.json()) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  const post = <T>(path: string, body: unknown) => call<T>(path, { method: 'POST', body: JSON.stringify(body) });

  // Try the real endpoint; fall back to the mock only for a stub endpoint, and only with dev mocks on.
  async function stub<T>(real: () => Promise<T>, mock: () => T): Promise<Maybe<T>> {
    try {
      return { data: await real(), mocked: false };
    } catch (e) {
      if (cfg.devMocks && e instanceof ApiError && NOT_BUILT.has(e.status)) return { data: mock(), mocked: true };
      throw e;
    }
  }

  const q = encodeURIComponent;

  return {
    health: () => call<Health>('/health'),
    lots: () => call<Lot[]>('/lots'),
    overview: (lot: string) => call<LotOverview>(`/lots/${q(lot)}/overview`),
    audit: (lot: string, limit = 50) => call<AuditEntry[]>(`/audit?lot_id=${q(lot)}&limit=${limit}`),
    qrKey: () => call<QrPublicKey>('/keys/qr'),
    bookingQr: (bookingId: string) => call<BookingQr>(`/bookings/${q(bookingId)}/qr`),
    rewards: (userId: string) => stub(() => call<Rewards>(`/users/${q(userId)}/rewards`),
      (): Rewards => ({ user_id: userId, points: 0, grace_minutes: 0, tier: 'none' })),

    // Attendant actions go through the outbox (src/core/sync.ts), never straight to /scan etc.
    syncPull: (lot: string, since?: string | null) =>
      call<SyncPull>(`/sync/pull?lot_id=${q(lot)}${since ? `&since=${q(since)}` : ''}`),
    syncPush: (events: SyncEvent[]) => post<SyncPushResult>('/sync/push', { device_id: cfg.deviceId, events }),
  };
}

export type Api = ReturnType<typeof createApi>;

// What a typed or scanned string means: a signed booking QR, a booking ID, or a plate.
export function classifyInput(raw: string): ScanInput | null {
  const s = raw.trim();
  if (!s) return null;
  // GPG1 gate codes are for drivers; treat them as qr so they read as invalid_qr, not not_found.
  if (/^GPG?1\./.test(s)) return { qr: s };
  if (/^B\d{3,}$/i.test(s)) return { booking_id: s.toUpperCase() };
  return { plate: s };
}
