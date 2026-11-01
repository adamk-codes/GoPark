import { sha256 } from '@noble/hashes/sha2.js';

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');

// UUIDv7 (RFC 9562): 48-bit ms timestamp, then random bits. Sortable, so the outbox keeps order.
// `randomBytes` is injected: expo-crypto in the app, node's crypto in tests.
export function uuidv7(randomBytes: (n: number) => Uint8Array, nowMs = Date.now()): string {
  const b = randomBytes(16);
  let ms = nowMs;
  for (let i = 5; i >= 0; i--) {
    b[i] = ms % 256;
    ms = Math.floor(ms / 256);
  }
  b[6] = (b[6] & 0x0f) | 0x70;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = hex(b);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// The stay id the server gives a walk-in (api/ops/routes.py walk_in), so offline walk-ins can be checked
// out before they sync.
export function walkInStayId(clientEventId: string): string {
  // Client event ids are ASCII (UUIDs), so char codes are the UTF-8 bytes the server hashes.
  return `W-${hex(sha256(Uint8Array.from(clientEventId, (ch) => ch.charCodeAt(0)))).slice(0, 16)}`;
}

// CONTRACT §1: uppercase, Arabic-Indic digits to 0–9, no spaces or dashes.
export function normPlate(p: string): string {
  const arabic = '٠١٢٣٤٥٦٧٨٩';
  return p.toUpperCase().replace(/[٠-٩]/g, (d) => String(arabic.indexOf(d))).replace(/[\s-]/g, '');
}

export const PLATE_RE = /^[A-Z]\d{1,6}$/;
