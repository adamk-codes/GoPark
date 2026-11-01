// The phone's local cache and outbox (expo-sqlite, included in Expo Go). Rows are stored as JSON: the
// sync engine keeps everything in memory and only needs this to survive restarts.
import * as SQLite from 'expo-sqlite';
import type { ConflictItem, OutboxItem, Rows, Snapshot, Store } from '../core/store';

const SCHEMA = `
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookings (id TEXT PRIMARY KEY, lot_id TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stays (id TEXT PRIMARY KEY, lot_id TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, lot_id TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, seq INTEGER NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts (id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS used_qr (bid TEXT PRIMARY KEY, at TEXT NOT NULL);
`;

type JsonRow = { json: string };

export class SqliteStore implements Store {
  private constructor(private db: SQLite.SQLiteDatabase) {}

  static async open(name = 'gopark-cache.db') {
    const db = await SQLite.openDatabaseAsync(name);
    await db.execAsync(SCHEMA);
    return new SqliteStore(db);
  }

  async load(): Promise<Snapshot> {
    const all = async <T>(sql: string) => (await this.db.getAllAsync<JsonRow>(sql)).map((r) => JSON.parse(r.json) as T);
    const meta = Object.fromEntries(
      (await this.db.getAllAsync<{ k: string; v: string }>('SELECT k, v FROM meta')).map((r) => [r.k, r.v]));
    return {
      meta,
      bookings: await all('SELECT json FROM bookings'),
      stays: await all('SELECT json FROM stays'),
      alerts: await all('SELECT json FROM alerts'),
      outbox: await all<OutboxItem>('SELECT json FROM outbox ORDER BY seq'),
      conflicts: await all<ConflictItem>('SELECT json FROM conflicts'),
      used: (await this.db.getAllAsync<{ bid: string }>('SELECT bid FROM used_qr')).map((r) => r.bid),
    };
  }

  async setMeta(key: string, value: string) {
    await this.db.runAsync('INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)', key, value);
  }

  async replaceLot(lotId: string, rows: Required<Rows>) {
    await this.db.withTransactionAsync(async () => {
      await this.db.runAsync('DELETE FROM bookings WHERE lot_id = ?', lotId);
      await this.db.runAsync('DELETE FROM stays WHERE lot_id = ?', lotId);
      await this.db.runAsync('DELETE FROM alerts WHERE lot_id = ?', lotId);
      await this.insert(rows);
    });
  }

  async upsert(rows: Rows) {
    await this.db.withTransactionAsync(() => this.insert(rows));
  }

  private async insert(rows: Rows) {
    for (const b of rows.bookings ?? []) {
      await this.db.runAsync('INSERT OR REPLACE INTO bookings (id, lot_id, json) VALUES (?, ?, ?)',
        b.booking_id, b.location_id, JSON.stringify(b));
    }
    for (const s of rows.stays ?? []) {
      await this.db.runAsync('INSERT OR REPLACE INTO stays (id, lot_id, json) VALUES (?, ?, ?)',
        s.id, s.lot_id, JSON.stringify(s));
    }
    for (const a of rows.alerts ?? []) {
      await this.db.runAsync('INSERT OR REPLACE INTO alerts (id, lot_id, json) VALUES (?, ?, ?)',
        a.id, a.lot_id, JSON.stringify(a));
    }
  }

  async putOutbox(item: OutboxItem) {
    // seq keeps queue order across restarts; an update keeps the original seq.
    await this.db.runAsync(
      'INSERT INTO outbox (id, seq, json) VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM outbox), ?) '
      + 'ON CONFLICT(id) DO UPDATE SET json = excluded.json',
      item.event.client_event_id, JSON.stringify(item));
  }

  async removeOutbox(ids: string[]) {
    if (!ids.length) return;
    await this.db.runAsync(`DELETE FROM outbox WHERE id IN (${ids.map(() => '?').join(',')})`, ids);
  }

  async putConflict(c: ConflictItem) {
    await this.db.runAsync('INSERT OR REPLACE INTO conflicts (id, json) VALUES (?, ?)', c.id, JSON.stringify(c));
  }

  async removeConflict(id: string) {
    await this.db.runAsync('DELETE FROM conflicts WHERE id = ?', id);
  }

  async addUsed(bookingId: string, at: string) {
    await this.db.runAsync('INSERT OR IGNORE INTO used_qr (bid, at) VALUES (?, ?)', bookingId, at);
  }
}
