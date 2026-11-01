// Offline check of signed booking QR codes (CONTRACT.md §5). Pure TS: runs under Hermes and in node tests.
// @noble/ed25519 needs a synchronous SHA-512; Hermes has no crypto.subtle, so it comes from @noble/hashes.
import * as ed from '@noble/ed25519';
import { sha512 } from '@noble/hashes/sha2.js';

ed.hashes.sha512 = sha512;

export interface QrPayload {
  bid: string;
  lot: string;
  ph: string; // sha256(plate_norm)[:16]
  s: string; // booking start, ISO with offset
  e: string; // booking end, ISO with offset
  n: string;
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const B64_INDEX: Record<string, number> = Object.fromEntries([...B64].map((ch, i) => [ch, i]));

// base64url without padding -> bytes. Hand-rolled so it doesn't depend on atob in the JS engine.
export function fromB64url(s: string): Uint8Array {
  const clean = s.replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
  if (clean.length % 4 === 1) throw new Error('bad base64url length');
  const out = new Uint8Array(Math.floor((clean.length * 3) / 4));
  let bits = 0;
  let acc = 0;
  let j = 0;
  for (const ch of clean) {
    const v = B64_INDEX[ch];
    if (v === undefined) throw new Error('bad base64url character');
    acc = (acc << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[j++] = (acc >> bits) & 0xff;
    }
  }
  return out;
}

const asciiBytes = (s: string) => Uint8Array.from(s, (ch) => ch.charCodeAt(0) & 0xff);
const utf8 = (b: Uint8Array) => decodeURIComponent(Array.from(b, (x) => `%${x.toString(16).padStart(2, '0')}`).join(''));

const FIELDS = ['bid', 'lot', 'ph', 's', 'e', 'n'] as const;

// The payload if `qr` is a well-formed GP1 code signed by `publicKeyB64`, else null. Same rules as api/ops/qr.py.
export function verifyBookingQr(qr: string, publicKeyB64: string): QrPayload | null {
  try {
    const parts = qr.trim().split('.');
    if (parts.length !== 3 || parts[0] !== 'GP1') return null;
    const [prefix, encoded, signature] = parts;
    const sig = fromB64url(signature);
    const pub = fromB64url(publicKeyB64);
    if (sig.length !== 64 || pub.length !== 32) return null;
    // zip215: false follows RFC 8032, like the server's `cryptography` library.
    if (!ed.verify(sig, asciiBytes(`${prefix}.${encoded}`), pub, { zip215: false })) return null;
    const payload = JSON.parse(utf8(fromB64url(encoded))) as Record<string, unknown>;
    const keys = Object.keys(payload);
    if (keys.length !== FIELDS.length || !FIELDS.every((k) => typeof payload[k] === 'string' && payload[k])) return null;
    return payload as unknown as QrPayload;
  } catch {
    return null;
  }
}

// The payload without checking the signature: only for display (e.g. which booking an invalid code claims).
export function peekBookingQr(qr: string): Partial<QrPayload> | null {
  try {
    const [prefix, encoded] = qr.trim().split('.');
    if (prefix !== 'GP1' || !encoded) return null;
    return JSON.parse(utf8(fromB64url(encoded)));
  } catch {
    return null;
  }
}
