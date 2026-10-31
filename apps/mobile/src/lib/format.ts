// The API returns Beirut local time with an offset. Read clock digits straight from the string so the
// phone's own timezone and clock never leak into the demo (the demo clock runs in November 2026).
export function hhmm(iso: string | null | undefined): string {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : '–';
}

export function minutesBetween(fromIso: string, toIso: string): number {
  return Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 60000);
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
