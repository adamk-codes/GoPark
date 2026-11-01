// Persistence behind the sync engine. The app uses SQLite (src/lib/sqlite-store.ts); node tests use MemoryStore.
import type { Alert, Booking, ScanResult, Stay, SyncEvent } from '../contract';

export interface OutboxItem {
  event: SyncEvent;
  local: ScanResult | null; // what the phone showed; its stay is applied on top of the cache until synced
  attempts: number;
  lastError: string | null;
}

export interface ConflictItem {
  id: string; // client_event_id
  event: SyncEvent;
  reason: string;
  at: string; // demo time the server refused it
}

export interface Snapshot {
  meta: Record<string, string>;
  bookings: Booking[];
  stays: Stay[];
  alerts: Alert[];
  outbox: OutboxItem[];
  conflicts: ConflictItem[];
  used: string[];
}

export interface Rows {
  bookings?: Booking[];
  stays?: Stay[];
  alerts?: Alert[];
}

export interface Store {
  load(): Promise<Snapshot>;
  setMeta(key: string, value: string): Promise<void>;
  replaceLot(lotId: string, rows: Required<Rows>): Promise<void>; // full pull: drop the lot's rows, then insert
  upsert(rows: Rows): Promise<void>;
  putOutbox(item: OutboxItem): Promise<void>; // insert or update by client_event_id
  removeOutbox(ids: string[]): Promise<void>;
  putConflict(c: ConflictItem): Promise<void>;
  removeConflict(id: string): Promise<void>;
  addUsed(bookingId: string, at: string): Promise<void>;
}

export class MemoryStore implements Store {
  snap: Snapshot = { meta: {}, bookings: [], stays: [], alerts: [], outbox: [], conflicts: [], used: [] };

  async load() {
    return JSON.parse(JSON.stringify(this.snap)) as Snapshot;
  }
  async setMeta(key: string, value: string) {
    this.snap.meta[key] = value;
  }
  async replaceLot(lotId: string, rows: Required<Rows>) {
    this.snap.bookings = this.snap.bookings.filter((b) => b.location_id !== lotId);
    this.snap.stays = this.snap.stays.filter((s) => s.lot_id !== lotId);
    this.snap.alerts = this.snap.alerts.filter((a) => a.lot_id !== lotId);
    await this.upsert(rows);
  }
  async upsert(rows: Rows) {
    const put = <T>(list: T[], items: T[] | undefined, key: (x: T) => string | number) => {
      for (const it of items ?? []) {
        const i = list.findIndex((x) => key(x) === key(it));
        if (i >= 0) list[i] = it;
        else list.push(it);
      }
    };
    put(this.snap.bookings, rows.bookings, (b) => b.booking_id);
    put(this.snap.stays, rows.stays, (s) => s.id);
    put(this.snap.alerts, rows.alerts, (a) => a.id);
  }
  async putOutbox(item: OutboxItem) {
    const i = this.snap.outbox.findIndex((o) => o.event.client_event_id === item.event.client_event_id);
    if (i >= 0) this.snap.outbox[i] = item;
    else this.snap.outbox.push(item);
  }
  async removeOutbox(ids: string[]) {
    this.snap.outbox = this.snap.outbox.filter((o) => !ids.includes(o.event.client_event_id));
  }
  async putConflict(c: ConflictItem) {
    this.snap.conflicts = [...this.snap.conflicts.filter((x) => x.id !== c.id), c];
  }
  async removeConflict(id: string) {
    this.snap.conflicts = this.snap.conflicts.filter((x) => x.id !== id);
  }
  async addUsed(bookingId: string) {
    if (!this.snap.used.includes(bookingId)) this.snap.used.push(bookingId);
  }
}
