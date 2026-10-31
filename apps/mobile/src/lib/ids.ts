import * as Crypto from 'expo-crypto';

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');

// UUIDv7 (RFC 9562): 48-bit ms timestamp, then random bits. Sortable, so the outbox keeps order.
export function uuidv7(): string {
  const b = Crypto.getRandomBytes(16);
  let ms = Date.now();
  for (let i = 5; i >= 0; i--) {
    b[i] = ms % 256;
    ms = Math.floor(ms / 256);
  }
  b[6] = (b[6] & 0x0f) | 0x70;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = hex(b);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function newDeviceId(): string {
  return `dev-attendant-${hex(Crypto.getRandomBytes(3))}`;
}
