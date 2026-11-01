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

// ---------- driver (phase P3) ----------
// TODO(P2): ops is adding these to the contract. Until then they follow the shapes announced for P3, read through
// the normalizers below so a field rename only touches this file; switch to contract.ts types once they land.
export type ConfirmAnswer = 'arrived' | 'not_me' | 'left';

export interface ConfirmRequest {
  device_id: string;
  client_event_id: string;
  answer: ConfirmAnswer;
  observed_at?: string; // when the driver tapped (sent later if offline)
}

// GET /users/{id}/pending_confirmations: stays the server wants the driver to confirm.
export interface PendingConfirmation {
  stay_id: string;
  booking_id: string | null;
  lot_id: string;
  question: 'arrival' | 'departure';
  asked_at: string | null;
}

export interface RewardEntry {
  at: string | null;
  points: number;
  reason: string;
}

export interface Perk {
  id: string;
  label: string;
  detail: string | null;
}

// GET /users/{id}/rewards
export interface Rewards {
  user_id: string;
  points: number;
  streak_weeks: number;
  perks: Perk[];
  history: RewardEntry[];
}

type Loose = Record<string, unknown>;
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

export function normalizeConfirmations(raw: unknown): PendingConfirmation[] {
  const list = Array.isArray(raw) ? raw : Array.isArray((raw as Loose)?.items) ? ((raw as Loose).items as unknown[]) : [];
  return list.flatMap((x) => {
    const r = (x ?? {}) as Loose;
    const stay = str(r.stay_id) ?? str(r.id);
    if (!stay) return [];
    const q = String(r.question ?? r.kind ?? r.type ?? r.direction ?? 'arrival');
    return [{ stay_id: stay, booking_id: str(r.booking_id), lot_id: str(r.lot_id) ?? '',
      question: /depart|leave|left|exit/.test(q) ? 'departure' : 'arrival',
      asked_at: str(r.asked_at) ?? str(r.created_at) ?? str(r.since) }];
  });
}

export function normalizeRewards(userId: string, raw: unknown): Rewards {
  const r = (raw ?? {}) as Loose;
  const perks = (Array.isArray(r.perks) ? r.perks : []).map((p, i): Perk => {
    if (typeof p === 'string') return { id: `perk-${i}`, label: p, detail: null };
    const o = (p ?? {}) as Loose;
    return { id: str(o.id) ?? `perk-${i}`, label: str(o.label) ?? str(o.name) ?? str(o.id) ?? '?',
      detail: str(o.detail) ?? str(o.description) };
  });
  const history = (Array.isArray(r.history) ? r.history : []).map((h): RewardEntry => {
    const o = (h ?? {}) as Loose;
    return { at: str(o.at) ?? str(o.created_at), points: num(o.points ?? o.delta), reason: str(o.reason) ?? '' };
  });
  return { user_id: str(r.user_id) ?? userId, points: num(r.points), streak_weeks: num(r.streak_weeks), perks, history };
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
    rewards: (userId: string) => stub(
      async () => normalizeRewards(userId, await call<unknown>(`/users/${q(userId)}/rewards`)),
      () => mockRewards(userId)),
    pendingConfirmations: (userId: string) => stub(
      async () => normalizeConfirmations(await call<unknown>(`/users/${q(userId)}/pending_confirmations`)),
      (): PendingConfirmation[] => [
        { stay_id: 'S-MOCK-ARRIVE', booking_id: 'B000000', lot_id: 'AUB', question: 'arrival', asked_at: null },
        { stay_id: 'S-MOCK-LEAVE', booking_id: 'B000001', lot_id: 'AUB', question: 'departure', asked_at: null },
      ]),
    // Driver confirms and signals normally go through the driver outbox (src/lib/driver.ts), which posts these.
    confirm: (stayId: string, body: ConfirmRequest) => post<unknown>(`/stays/${q(stayId)}/confirm`, body),

    // Attendant actions go through the outbox (src/core/sync.ts), never straight to /scan etc.
    syncPull: (lot: string, since?: string | null) =>
      call<SyncPull>(`/sync/pull?lot_id=${q(lot)}${since ? `&since=${q(since)}` : ''}`),
    syncPush: (events: SyncEvent[]) => post<SyncPushResult>('/sync/push', { device_id: cfg.deviceId, events }),
  };
}

export type Api = ReturnType<typeof createApi>;

function mockRewards(userId: string): Rewards {
  return { user_id: userId, points: 120, streak_weeks: 3, perks: [
    { id: 'grace-10', label: '10 grace minutes', detail: 'Leave up to 10 min late without an overstay fee' },
    { id: 'priority', label: 'Priority spot', detail: 'Your usual spot is held 15 min longer' },
  ], history: [
    { at: null, points: 20, reason: 'Confirmed arrival with one tap' },
    { at: null, points: 50, reason: '3-week booking streak' },
  ] };
}

// What a typed or scanned string means: a signed booking QR, a booking ID, or a plate.
export function classifyInput(raw: string): ScanInput | null {
  const s = raw.trim();
  if (!s) return null;
  // GPG1 gate codes are for drivers; treat them as qr so they read as invalid_qr, not not_found.
  if (/^GPG?1\./.test(s)) return { qr: s };
  if (/^B\d{3,}$/i.test(s)) return { booking_id: s.toUpperCase() };
  return { plate: s };
}
