// The driver phone's outbox: POSTs (one-tap confirms, geofence signals) that must reach the server even if the
// phone is offline when they happen. Each carries its client_event_id and original observed_at, so a late or
// repeated delivery is harmless (the server is idempotent on client_event_id). Not /sync/push: that endpoint
// only takes attendant event types. Pure TS over a small key-value store, so the background geofence task
// (no React mounted) and the screens share one queue.

export interface QueuedRequest {
  id: string; // client_event_id
  kind: 'confirm' | 'signal';
  path: string;
  body: object;
  label: string; // shown in the UI, e.g. "Arrived at AUB"
  queuedAt: string; // demo time
  attempts: number;
  lastError: string | null;
}

export interface FailedRequest extends QueuedRequest {
  status: number;
  reason: string;
}

// Delivered, with the server's answer (e.g. a ConfirmResult with points_awarded), newest first.
export interface DoneRequest extends QueuedRequest {
  response: unknown;
}

export interface OutboxState {
  pending: QueuedRequest[];
  failed: FailedRequest[]; // refused by the server (4xx); kept so the driver can see it, newest first
  done: DoneRequest[]; // last few delivered, until the driver dismisses them
  online: boolean | null;
}

export interface KV {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
}

// Throws on network failure; otherwise the HTTP status and body text.
export type Send = (path: string, body: unknown) => Promise<{ status: number; text: string }>;

const RETRY = new Set([408, 425, 429]);
const MAX_FAILED = 20;
const MAX_DONE = 10;

export class RequestOutbox {
  private lock: Promise<unknown> = Promise.resolve();

  constructor(private kv: KV, private key = 'gopark.driver.outbox.v1') {}

  async state(): Promise<OutboxState> {
    const raw = await this.kv.get(this.key);
    const s = raw ? (JSON.parse(raw) as Partial<OutboxState>) : {};
    return { pending: s.pending ?? [], failed: s.failed ?? [], done: s.done ?? [], online: s.online ?? null };
  }

  enqueue(req: Omit<QueuedRequest, 'attempts' | 'lastError'>): Promise<void> {
    return this.locked(async () => {
      const s = await this.state();
      if (!s.pending.some((p) => p.id === req.id)) s.pending.push({ ...req, attempts: 0, lastError: null });
      await this.save(s);
    });
  }

  // Forget a refused or delivered request the driver has seen.
  dismiss(id: string): Promise<void> {
    return this.locked(async () => {
      const s = await this.state();
      s.failed = s.failed.filter((f) => f.id !== id);
      s.done = s.done.filter((d) => d.id !== id);
      await this.save(s);
    });
  }

  // Send everything queued, oldest first. Stops at the first network failure (offline: the rest would fail too).
  flush(send: Send): Promise<OutboxState> {
    return this.locked(async () => {
      const s = await this.state();
      const keep: QueuedRequest[] = [];
      let offline = false;
      for (const req of s.pending) {
        if (offline) {
          keep.push(req);
          continue;
        }
        let res: { status: number; text: string };
        try {
          res = await send(req.path, req.body);
        } catch (e) {
          offline = true;
          keep.push({ ...req, attempts: req.attempts + 1, lastError: e instanceof Error ? e.message : String(e) });
          continue;
        }
        s.online = true;
        if (res.status < 300) {
          s.done = [{ ...req, response: parse(res.text) }, ...s.done].slice(0, MAX_DONE);
          continue;
        }
        if (res.status >= 500 || RETRY.has(res.status)) {
          keep.push({ ...req, attempts: req.attempts + 1, lastError: `${res.status}` });
          continue;
        }
        s.failed = [{ ...req, status: res.status, reason: detail(res.text) }, ...s.failed].slice(0, MAX_FAILED);
      }
      if (offline) s.online = false;
      s.pending = keep;
      await this.save(s);
      return s;
    });
  }

  private save(s: OutboxState) {
    return this.kv.set(this.key, JSON.stringify(s));
  }

  // One read-modify-write at a time within this JS context.
  private locked<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.lock.then(fn, fn);
    this.lock = run.catch(() => undefined);
    return run;
  }
}

function parse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// FastAPI errors look like {"detail": "..."}; show just the message.
function detail(text: string): string {
  try {
    const d = JSON.parse(text)?.detail;
    if (typeof d === 'string') return d;
    if (d) return JSON.stringify(d);
  } catch {
    // not JSON
  }
  return text.slice(0, 200);
}
