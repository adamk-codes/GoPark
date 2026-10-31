import { useEffect, useState } from 'react'
import type { AuditEntry, Lot, LotForecast, LotOverview, PatternRow, RunInfo, SignalKind, SignalSource, Stay, UserRhythm } from './contract'

export interface Alert {
  type: string
  severity: 'info' | 'warning' | 'critical'
  lot_id: string
  stay_id: string | null
  booking_id: string | null
  message: string
  created_at: string
}

// TODO(P2): drop these once P2's contract PR (GET /stays/{id}/evidence) lands and `npm run contract` copies it.
// Mirrors StayEvidence / EvidenceStep on P2's branch.
export interface EvidenceStep {
  at: string
  source: SignalSource
  kind: SignalKind
  contribution: number | null // log-likelihood added after freshness decay; null when definitive
  p_after: number
  definitive?: boolean
  phase?: 'arrival' | 'departure'
  client_event_id?: string | null
}
export interface StayEvidence {
  stay_id: string
  booking_id: string | null
  lot_id: string
  prior: number
  prior_source: 'rhythm' | 'default' | 'none'
  p: number | null
  thresholds: { auto_confirm: number; ask_driver: number; attendant_after_min: number }
  timeline: EvidenceStep[]
  mocked?: boolean // dashboard-only: true when built by mockEvidence()
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

export const api = {
  health: () => get<{ ok: boolean; now: string; version: string }>('/health'),
  lots: () => get<Lot[]>('/lots'),
  overview: (lot: string) => get<LotOverview>(`/lots/${lot}/overview`),
  stays: (lot: string, state?: string) => get<Stay[]>(`/stays?lot_id=${lot}${state ? `&state=${state}` : ''}`),
  alerts: (lot: string) => get<Alert[]>(`/alerts?lot_id=${lot}`),
  audit: (lot: string) => get<AuditEntry[]>(`/audit?lot_id=${lot}&limit=15`),
  run: () => get<RunInfo>('/rhythm/runs/latest'),
  patterns: (primaryOnly = true) => get<PatternRow[]>(`/rhythm/runs/latest/patterns${primaryOnly ? '?primary_only=true' : ''}`),
  forecast: (lot: string, date: string) => get<LotForecast[]>(`/rhythm/runs/latest/forecast?lot_id=${lot}&date=${date}`),
  user: (id: string) => get<UserRhythm>(`/rhythm/users/${encodeURIComponent(id)}`),
  evidence: async (stay: Stay): Promise<StayEvidence> => {
    try {
      const ev = await get<StayEvidence>(`/stays/${stay.id}/evidence`)
      if (ev.timeline.length) return ev
      return mockEvidence(stay, ev)
    } catch {
      return mockEvidence(stay)
    }
  },
  exportUrl: (format: 'csv' | 'json') => `/api/rhythm/runs/latest/patterns?format=${format}`,
}

/** Demo clock from the API (never the browser clock). */
export function useDemoNow(pollMs = 15000): string | null {
  const [now, setNow] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => api.health().then((h) => alive && setNow(h.now)).catch(() => {})
    load()
    const id = setInterval(load, pollMs)
    return () => { alive = false; clearInterval(id) }
  }, [pollMs])
  return now
}

// ---------------------------------------------------------------- mock evidence
// Starting likelihood ratios from ARCHITECTURE_PLAN §3.1, so the mock tells the same story fusion will.
const LR: Partial<Record<SignalSource, number>> = { geofence: 4, motion: 8, ble: 15, car_bt: 10, camera_plate: 50, camera_spot: 6, nfc: 100, gate_qr: 200 }
const DEFINITIVE: SignalSource[] = ['attendant', 'booking_qr', 'driver_confirm']
const logit = (p: number) => Math.log(p / (1 - p))
const sigmoid = (x: number) => 1 / (1 + Math.exp(-x))

function hash(s: string) {
  let h = 2166136261
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  return h >>> 0
}

/** Plausible evidence for a stay, shaped like P2's response, until fusion steps exist. */
export function mockEvidence(stay: Stay, base?: StayEvidence): StayEvidence {
  const h = hash(stay.id)
  const prior = base?.prior ?? 0.35 + (h % 30) / 100
  const thresholds = base?.thresholds ?? { auto_confirm: 0.95, ask_driver: 0.6, attendant_after_min: 10 }
  const t0 = Date.parse(stay.in_at ?? new Date(0).toISOString())
  const plan: [number, SignalSource, SignalKind, number?][] =
    stay.in_method === 'auto' ? [[-3, 'geofence', 'enter'], [1, 'motion', 'park'], [2, 'camera_plate', 'enter']]
      : stay.in_method === 'driver' ? [[2, 'geofence', 'enter', 0.6], [6, 'driver_confirm', 'confirm']]
        : [[-1, 'geofence', 'enter', 0.4], [9, 'attendant', 'scan']]
  let x = logit(prior)
  const timeline: EvidenceStep[] = plan.sort((a, b) => a[0] - b[0]).map(([min, source, kind, fresh = 1]) => {
    const at = new Date(t0 + min * 60000 + (h % 50) * 1000)
    if (DEFINITIVE.includes(source)) {
      x = logit(0.999)
      return { at: localIso(at, stay.in_at), source, kind, contribution: null, p_after: 0.999, definitive: true, phase: 'arrival' }
    }
    const c = Math.log(LR[source] ?? 2) * fresh
    x += c
    return { at: localIso(at, stay.in_at), source, kind, contribution: Math.round(c * 100) / 100, p_after: sigmoid(x), phase: 'arrival' }
  })
  return {
    stay_id: stay.id, booking_id: stay.booking_id, lot_id: stay.lot_id, prior,
    prior_source: base?.prior_source ?? (stay.booking_id ? 'rhythm' : 'none'),
    p: timeline.at(-1)?.p_after ?? null, thresholds, timeline, mocked: true,
  }
}

/** Format a Date in the same UTC offset as a reference ISO string from the API. */
function localIso(d: Date, ref: string | null): string {
  const off = ref?.match(/([+-]\d\d):(\d\d)$/)
  const mins = off ? (off[1].startsWith('-') ? -1 : 1) * (Math.abs(+off[1]) * 60 + +off[2]) : 0
  const shifted = new Date(d.getTime() + mins * 60000).toISOString().slice(0, 19)
  return off ? `${shifted}${off[0]}` : `${shifted}Z`
}

// ---------------------------------------------------------------- formatting
export const TZ = 'Asia/Beirut'
export const hhmm = (iso: string | null) => (iso ? iso.slice(11, 16) : '—')
export const pct = (p: number) => `${Math.round(p * 100)}%`
/** Like pct, but never rounds a probability just under 1 up to "100%". */
export const pctFine = (p: number) => (p >= 0.995 && p < 1 ? `${Math.floor(p * 1000) / 10}%` : pct(p))
export const isoDate = (iso: string) => iso.slice(0, 10)
export const dayTime = (iso: string) =>
  new Date(iso).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: TZ })
export const dayLabel = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: TZ })
export const label = (s: string) => s.replaceAll('_', ' ')
export const openUser = (id: string) => { location.hash = `user/${encodeURIComponent(id)}` }
