// Live state for one lot. Polls every 5 s. With VITE_LIVE_WS=1 it also opens `WS /live?lot_id=` (pushes
// `{type: "stay"|"alert", data}`, CONTRACT.md §7) and merges pushed stays/alerts between polls; polling stays
// on as the fallback (and for overview counts and the audit list, which the socket doesn't carry).
// Flip the default once the ops API with /live is on main; pages don't change.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Alert, AuditEntry, LotOverview, Stay } from './contract'

export const LIVE_POLL_MS = 5000
const WS_ENABLED = import.meta.env.VITE_LIVE_WS === '1'

export interface LiveState {
  overview: LotOverview | null
  stays: Stay[]
  alerts: Alert[]
  audit: AuditEntry[]
  error: string | null
  /** When the last update arrived (browser time, only used for the "updated" hint). */
  updatedAt: number | null
  transport: 'poll' | 'ws'
  /** Fetch everything now, e.g. after resolving an alert. */
  refresh: () => void
  /** Apply a local change before the next update arrives (optimistic UI). */
  patchAlert: (a: Alert) => void
}

type LiveMessage = { type: 'stay'; data: Stay } | { type: 'alert'; data: Alert }

/** Replace-or-insert by id; stays come newest first like GET /stays. */
function upsert<T extends { id: string | number }>(xs: T[], x: T): T[] {
  const i = xs.findIndex((y) => y.id === x.id)
  return i < 0 ? [x, ...xs] : xs.map((y, j) => (j === i ? x : y))
}

export function useLive(lot: string): LiveState {
  const [overview, setOverview] = useState<LotOverview | null>(null)
  const [stays, setStays] = useState<Stay[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [transport, setTransport] = useState<'poll' | 'ws'>('poll')
  const loadRef = useRef<() => void>(() => {})

  useEffect(() => {
    let alive = true
    setOverview(null); setStays([]); setAlerts([]); setAudit([])
    const load = () =>
      Promise.all([api.overview(lot), api.stays(lot), api.alerts(lot), api.audit(lot)])
        .then(([o, s, a, l]) => {
          if (!alive) return
          setOverview(o); setStays(s); setAlerts(a); setAudit(l); setError(null); setUpdatedAt(Date.now())
        })
        .catch((e) => alive && setError(`Can't reach the API: ${e}`))
    loadRef.current = load
    load()
    const id = setInterval(load, LIVE_POLL_MS)

    let ws: WebSocket | null = null
    if (WS_ENABLED) {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/live?lot_id=${encodeURIComponent(lot)}`)
      ws.onopen = () => alive && setTransport('ws')
      ws.onclose = () => alive && setTransport('poll')
      ws.onmessage = (ev) => {
        if (!alive) return
        const msg = JSON.parse(ev.data) as LiveMessage
        if (msg.type === 'stay') setStays((xs) => upsert(xs, msg.data))
        else if (msg.type === 'alert') setAlerts((xs) => (msg.data.status === 'open' ? upsert(xs, msg.data) : xs.filter((x) => x.id !== msg.data.id)))
        setUpdatedAt(Date.now())
      }
    }
    return () => { alive = false; clearInterval(id); ws?.close() }
  }, [lot])

  const refresh = useCallback(() => loadRef.current(), [])
  const patchAlert = useCallback((a: Alert) => setAlerts((xs) => xs.map((x) => (x.id === a.id ? a : x))), [])

  return { overview, stays, alerts, audit, error, updatedAt, transport, refresh, patchAlert }
}
