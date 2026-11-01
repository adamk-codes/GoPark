// The demo clock (docs/briefs/README.md rule 7). The server's clock runs in November 2026 and ticks in real
// time, so the phone keeps the offset to it and stamps offline actions with demo time, never its own clock.

export interface DemoClock {
  offsetMs: number; // server demo time minus phone time
  tz: string; // UTC offset of the server's local time, e.g. "+02:00" (Asia/Beirut, changes with DST)
}

export const PHONE_CLOCK: DemoClock = { offsetMs: 0, tz: '+02:00' };

// Learn the clock from any server timestamp, e.g. GET /health `now` ("2026-11-16T07:50:00+02:00").
export function clockFrom(serverNowIso: string, phoneNowMs = Date.now()): DemoClock {
  const m = /([+-]\d{2}:\d{2}|Z)$/.exec(serverNowIso);
  return { offsetMs: Date.parse(serverNowIso) - phoneNowMs, tz: m ? (m[1] === 'Z' ? '+00:00' : m[1]) : '+02:00' };
}

export function demoNowMs(clock: DemoClock, phoneNowMs = Date.now()): number {
  return phoneNowMs + clock.offsetMs;
}

// ISO 8601 with the server's offset, e.g. 2026-11-16T08:01:10+02:00.
export function isoAt(ms: number, tz: string): string {
  const sign = tz.startsWith('-') ? -1 : 1;
  const [h, m] = tz.slice(1).split(':').map(Number);
  const local = new Date(ms + sign * (h * 60 + m) * 60_000);
  return `${local.toISOString().slice(0, 19)}${tz}`;
}

export function demoNowIso(clock: DemoClock, phoneNowMs = Date.now()): string {
  return isoAt(demoNowMs(clock, phoneNowMs), clock.tz);
}
