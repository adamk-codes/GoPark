import { useEffect, useState } from 'react'
import type {
  Alert, AuditEntry, Lot, LotForecast, LotOverview, PatternRow, RunInfo, SignalKind, SignalSource, Stay, StayEvidence,
  StayEvidencePoint, UserRhythm,
} from './contract'
import { FUSION } from './fusionConfig'
import type { FusionMetrics, RhythmMetrics } from './metrics'

/** Evidence for one stay, plus where it came from: the API, or the labelled mock when the endpoint failed. */
export interface EvidenceResult {
  ev: StayEvidence
  mocked: boolean
  reason?: string
}

export const DASHBOARD_DEVICE = 'dashboard'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

// The ops simulator writes runs/fusion_metrics.json; no route serves it yet. Rather than probing guessed URLs
// (each miss is a console error), look the route up in FastAPI's OpenAPI document and fetch it only if it exists.
const FUSION_ROUTE = /(fusion|simulat|detection).*metric|metric.*(fusion|simulat|detection)/i

async function findFusionMetricsPath(): Promise<string | null> {
  const doc = await get<{ paths?: Record<string, unknown> }>('/openapi.json')
  return Object.keys(doc.paths ?? {}).find((p) => FUSION_ROUTE.test(p) && !p.includes('{')) ?? null
}

export const api = {
  health: () => get<{ ok: boolean; now: string; version: string }>('/health'),
  lots: () => get<Lot[]>('/lots'),
  overview: (lot: string) => get<LotOverview>(`/lots/${lot}/overview`),
  stays: (lot: string, state?: string) => get<Stay[]>(`/stays?lot_id=${lot}${state ? `&state=${state}` : ''}`),
  alerts: (lot: string) => get<Alert[]>(`/alerts?lot_id=${lot}`),
  alertsAll: () => get<Alert[]>('/alerts'),
  auditAll: (limit = 60) => get<(AuditEntry & { lot_id?: string | null })[]>(`/audit?limit=${limit}`),
  resolveAlert: (id: number, note?: string) => post<Alert>(`/alerts/${id}/resolve`, { device_id: DASHBOARD_DEVICE, note }),
  audit: (lot: string) => get<AuditEntry[]>(`/audit?lot_id=${lot}&limit=15`),
  run: () => get<RunInfo>('/rhythm/runs/latest'),
  patterns: (primaryOnly = true) => get<PatternRow[]>(`/rhythm/runs/latest/patterns${primaryOnly ? '?primary_only=true' : ''}`),
  forecast: (lot: string, date: string) => get<LotForecast[]>(`/rhythm/runs/latest/forecast?lot_id=${lot}&date=${date}`),
  user: (id: string) => get<UserRhythm>(`/rhythm/users/${encodeURIComponent(id)}`),
  evaluation: () => get<RhythmMetrics>('/rhythm/runs/latest/evaluation'),
  /** Fusion/simulator metrics, or null while no endpoint serves them yet. */
  fusionMetrics: async (): Promise<FusionMetrics | null> => {
    try {
      const path = await findFusionMetricsPath()
      return path ? await get<FusionMetrics>(path) : null
    } catch {
      return null
    }
  },
  /** Real evidence; the mock only stands in when the endpoint itself fails (not when it's empty). */
  evidence: async (stay: Stay): Promise<EvidenceResult> => {
    try {
      return { ev: await get<StayEvidence>(`/stays/${encodeURIComponent(stay.id)}/evidence`), mocked: false }
    } catch (e) {
      return { ev: mockEvidence(stay), mocked: true, reason: String(e) }
    }
  },
  exportUrl: (format: 'csv' | 'json') => `/api/rhythm/runs/latest/patterns?format=${format}`,
}

/** Lot list; retries every 5 s until the API answers, so a slow API start doesn't leave the picker empty. */
export function useLots(): Lot[] {
  const [lots, setLots] = useState<Lot[]>([])
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const load = () => api.lots().then((l) => alive && setLots(l)).catch(() => { if (alive) timer = setTimeout(load, 5000) })
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [])
  return lots
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
// Fallback only, used when GET /stays/{id}/evidence fails. Same maths as api/ops/fusion.py (log_lr from
// config/fusion.yaml, definitive sources jump to ±20 log-odds), so the story matches the real thing.
const logit = (p: number) => Math.log(p / (1 - p))
const sigmoid = (x: number) => 1 / (1 + Math.exp(-Math.max(-40, Math.min(40, x))))

function hash(s: string) {
  let h = 2166136261
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  return h >>> 0
}

type Plan = [minutes: number, source: SignalSource, kind: SignalKind, freshness?: number][]

export function mockEvidence(stay: Stay): StayEvidence {
  const h = hash(stay.id)
  const prior = 0.35 + (h % 30) / 100
  const t0 = Date.parse(stay.in_at ?? new Date(0).toISOString())
  const arrival: Plan =
    stay.in_method === 'auto' ? [[-3, 'geofence', 'enter'], [1, 'motion', 'park'], [2, 'camera_plate', 'enter']]
      : stay.in_method === 'driver' ? [[2, 'geofence', 'enter', 0.6], [6, 'driver_confirm', 'confirm']]
        : [[-1, 'geofence', 'enter', 0.4], [9, 'attendant', 'scan']]
  const run = (plan: Plan, direction: StayEvidencePoint['direction'], start: number, p0: number) => {
    let x = logit(p0)
    return plan.map(([min, source, kind, fresh = 1], i): StayEvidencePoint => {
      const lr = FUSION.logLr[source]
      const definitive = lr == null
      const log_lr = definitive ? 20 : lr
      const weighted_log_lr = definitive ? 20 : lr * fresh
      x = definitive ? 20 : x + weighted_log_lr
      return {
        signal_id: -(i + 1 + (direction === 'departure' ? 100 : 0)), direction,
        observed_at: localIso(new Date(start + min * 60000 + (h % 50) * 1000), stay.in_at),
        source, kind, log_lr, weighted_log_lr, probability: sigmoid(x),
      }
    })
  }
  const evidence = run(arrival, 'arrival', t0, prior)
  if (stay.out_at && stay.out_method === 'auto') {
    evidence.push(...run([[-1, 'motion', 'unpark'], [0, 'camera_plate', 'exit'], [2, 'geofence', 'exit']],
      'departure', Date.parse(stay.out_at), FUSION.defaultPrior))
  }
  return { stay_id: stay.id, prior, evidence }
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
