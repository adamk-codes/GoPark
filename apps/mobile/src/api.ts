import type { AuditEntry, Lot, LotOverview, ScanResult, Stay } from './contract';
import type {
  Alert, BookingQr, Health, QrPublicKey, ResolveAlertRequest, Rewards, WalkInRequest,
} from './pending-contract';

// Default server; Settings can override it. Put EXPO_PUBLIC_API_URL=http://<laptop-LAN-IP>:8000 in apps/mobile/.env.
export const DEFAULT_API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
// Endpoints P2 is still building are mocked on the phone while this is on (EXPO_PUBLIC_MOCK_PENDING=0 turns it off).
export const DEFAULT_MOCK_PENDING = process.env.EXPO_PUBLIC_MOCK_PENDING !== '0';

export interface ApiConfig {
  baseUrl: string;
  deviceId: string;
  mockPending: boolean;
  newId: () => string; // client_event_id generator (UUIDv7 in the app)
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

// A response from an endpoint that may still be mocked: `mocked` tells the UI to say so.
export interface Maybe<T> {
  data: T;
  mocked: boolean;
}

export type ScanInput = { qr: string } | { booking_id: string } | { plate: string };

// Status codes that mean "P2 hasn't shipped this endpoint yet".
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

  // Try the real endpoint; fall back to the mock only when it isn't built yet (and mocks are on).
  async function pending<T>(real: () => Promise<T>, mock: () => T): Promise<Maybe<T>> {
    try {
      return { data: await real(), mocked: false };
    } catch (e) {
      if (cfg.mockPending && e instanceof ApiError && NOT_BUILT.has(e.status)) return { data: mock(), mocked: true };
      throw e;
    }
  }

  return {
    health: () => call<Health>('/health'),
    lots: () => call<Lot[]>('/lots'),
    overview: (lot: string) => call<LotOverview>(`/lots/${encodeURIComponent(lot)}/overview`),
    stays: (lot: string, state?: string) =>
      call<Stay[]>(`/stays?lot_id=${encodeURIComponent(lot)}${state ? `&state=${state}` : ''}`),
    audit: (lot: string, limit = 50) => call<AuditEntry[]>(`/audit?lot_id=${encodeURIComponent(lot)}&limit=${limit}`),
    alerts: (lot: string) => call<Alert[]>(`/alerts?lot_id=${encodeURIComponent(lot)}`),
    rewards: (userId: string) => call<Rewards>(`/users/${encodeURIComponent(userId)}/rewards`),

    scan: (lot: string, input: ScanInput) =>
      post<ScanResult>('/scan', { lot_id: lot, device_id: cfg.deviceId, client_event_id: cfg.newId(), ...input }),

    checkout: (stayId: string) =>
      post<ScanResult>(`/stays/${encodeURIComponent(stayId)}/checkout`, {
        device_id: cfg.deviceId, client_event_id: cfg.newId(), method: 'attendant',
      }),

    // TODO(P2): POST /walk_ins. Mock: a local walk-in stay, not stored on the server.
    walkIn: (lot: string, plate: string, spaceId?: string | null) => {
      const body: WalkInRequest = {
        lot_id: lot, plate, device_id: cfg.deviceId, client_event_id: cfg.newId(), space_id: spaceId ?? null,
      };
      return pending(() => post<ScanResult>('/walk_ins', body), () => mockWalkIn(body));
    },

    // TODO(P2): POST /alerts/{id}/resolve. Mock: echo the alert back as resolved.
    resolveAlert: (alert: Alert, note?: string) => {
      const body: ResolveAlertRequest = { device_id: cfg.deviceId, client_event_id: cfg.newId(), note: note ?? null };
      const mock = (): Alert => ({ ...alert, status: 'resolved', resolved_by: cfg.deviceId, note: note ?? null });
      if (!alert.id) return Promise.resolve({ data: mock(), mocked: true });
      return pending(() => post<Alert>(`/alerts/${encodeURIComponent(alert.id!)}/resolve`, body), mock);
    },

    // TODO(P2): GET /keys/qr. Cached for offline QR checks in phase P2.
    qrKey: () => pending(() => call<QrPublicKey>('/keys/qr'),
      (): QrPublicKey => ({ format: 'GP1', alg: 'Ed25519', kid: 'mock', public_key: '' })),

    // TODO(P2): GET /bookings/{id}/qr. Mock: an unsigned GP1 string the server will reject as invalid_qr.
    bookingQr: (bookingId: string, lot: string) =>
      pending(() => call<BookingQr>(`/bookings/${encodeURIComponent(bookingId)}/qr`), () => mockBookingQr(bookingId, lot)),
  };
}

export type Api = ReturnType<typeof createApi>;

function normPlate(p: string) {
  const arabic = '٠١٢٣٤٥٦٧٨٩';
  return p.toUpperCase().replace(/[٠-٩]/g, (d) => String(arabic.indexOf(d))).replace(/[\s-]/g, '');
}

function mockWalkIn(b: WalkInRequest): ScanResult {
  return {
    result: 'checked_in',
    message: `Checked in walk-in ${b.plate.toUpperCase()} at ${b.lot_id}.`,
    booking: null,
    stay: {
      id: `W-${b.client_event_id}`, booking_id: null, lot_id: b.lot_id, plate_norm: normPlate(b.plate),
      state: 'CHECKED_IN', in_at: null, in_method: 'attendant', in_conf: 1, out_at: null, out_method: null,
      out_conf: null, inferred: false,
    },
    suggest: null,
  };
}

function b64url(s: string) {
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function mockBookingQr(bookingId: string, lot: string): BookingQr {
  const payload = b64url(JSON.stringify({ bid: bookingId, lot, ph: '', s: '', e: '', n: 'mock0000' }));
  return { booking_id: bookingId, qr: `GP1.${payload}.mock`, valid_from: '', valid_until: '' };
}

// What a typed or scanned string means: a signed booking QR, a booking ID, or a plate.
export function classifyInput(raw: string): ScanInput | null {
  const s = raw.trim();
  if (!s) return null;
  // GPG1 gate codes are for drivers; send them as qr too so the server answers invalid_qr, not not_found.
  if (/^GPG?1\./.test(s)) return { qr: s };
  if (/^B\d{3,}$/i.test(s)) return { booking_id: s.toUpperCase() };
  return { plate: s };
}
