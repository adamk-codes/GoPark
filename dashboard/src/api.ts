import type { AuditEntry, Lot, LotOverview, PatternRow, RunInfo, Stay } from './contract'

export interface Alert {
  type: string
  severity: 'info' | 'warning' | 'critical'
  lot_id: string
  stay_id: string | null
  booking_id: string | null
  message: string
  created_at: string
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

export const api = {
  lots: () => get<Lot[]>('/lots'),
  overview: (lot: string) => get<LotOverview>(`/lots/${lot}/overview`),
  stays: (lot: string) => get<Stay[]>(`/stays?lot_id=${lot}&state=CHECKED_IN`),
  alerts: (lot: string) => get<Alert[]>(`/alerts?lot_id=${lot}`),
  audit: (lot: string) => get<AuditEntry[]>(`/audit?lot_id=${lot}&limit=15`),
  run: () => get<RunInfo>('/rhythm/runs/latest'),
  patterns: () => get<PatternRow[]>('/rhythm/runs/latest/patterns?primary_only=true'),
  exportUrl: (format: 'csv' | 'json') => `/api/rhythm/runs/latest/patterns?format=${format}`,
}

export const hhmm = (iso: string | null) => (iso ? iso.slice(11, 16) : '—')
export const pct = (p: number) => `${Math.round(p * 100)}%`
