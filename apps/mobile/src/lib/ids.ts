import * as Crypto from 'expo-crypto';
import { uuidv7 as uuidv7From } from '../core/ids';

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');

// UUIDv7 client_event_id (sortable, so the outbox keeps order), with randomness from expo-crypto.
export const uuidv7 = () => uuidv7From((n) => Crypto.getRandomBytes(n));

export function newDeviceId(): string {
  return `dev-attendant-${hex(Crypto.getRandomBytes(3))}`;
}
