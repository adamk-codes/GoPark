import type {
  Alert, AuditEntry, Booking, BookingQr, ConfirmRequest, ConfirmResult, Lot, LotOverview, PendingConfirmation,
  QrPublicKey, Rewards, ScanResult, Stay, SyncEvent,
} from './contract';

// Default server; Settings can override it. Put EXPO_PUBLIC_API_URL=http://<laptop-LAN-IP>:8000 in apps/mobile/.env.
export const DEFAULT_API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

// Shapes the API returns that contract/types.ts doesn't list yet (from api/ops/routes.py on main).
// TODO(P2): move these into contract/types.ts.
export interface Health {
  ok: boolean;
  now: string;
  version: string;
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

export type ScanInput = { qr: string } | { booking_id: string } | { plate: string };

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

  const q = encodeURIComponent;

  return {
    health: () => call<Health>('/health'),
    lots: () => call<Lot[]>('/lots'),
    overview: (lot: string) => call<LotOverview>(`/lots/${q(lot)}/overview`),
    audit: (lot: string, limit = 50) => call<AuditEntry[]>(`/audit?lot_id=${q(lot)}&limit=${limit}`),
    qrKey: () => call<QrPublicKey>('/keys/qr'),
    bookingQr: (bookingId: string) => call<BookingQr>(`/bookings/${q(bookingId)}/qr`),
    rewards: (userId: string) => call<Rewards>(`/users/${q(userId)}/rewards`),
    pendingConfirmations: (userId: string) => call<PendingConfirmation[]>(`/users/${q(userId)}/pending_confirmations`),
    // Driver confirms and signals normally go through the driver outbox (src/lib/driver.ts), which posts these.
    confirm: (stayId: string, body: ConfirmRequest) => post<ConfirmResult>(`/stays/${q(stayId)}/confirm`, body),

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
