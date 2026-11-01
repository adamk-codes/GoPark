import { useState } from 'react'
import { api, hhmm, label } from './api'
import type { Alert, AlertType } from './contract'

/** Alert types that mean "fusion couldn't decide on its own": shown first and flagged for review. */
const FLAGGED: AlertType[] = ['low_confidence', 'anomaly']
const SEVERITY_RANK = { critical: 0, warning: 1, info: 2 } as const

const alertId = (id: number) => `A${String(id).padStart(6, '0')}`

export function AlertsPanel({ alerts, onResolved }: { alerts: Alert[]; onResolved: (a: Alert) => void }) {
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resolved, setResolved] = useState<Alert[]>([]) // GET /alerts lists open ones only; keep this session's resolutions visible
  const [showResolved, setShowResolved] = useState(false)

  const open = alerts.filter((a) => a.status === 'open' && !resolved.some((r) => r.id === a.id))
    .sort((a, b) => Number(FLAGGED.includes(b.type)) - Number(FLAGGED.includes(a.type))
      || SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.created_at.localeCompare(a.created_at))
  const flagged = open.filter((a) => FLAGGED.includes(a.type)).length

  const resolve = async (a: Alert) => {
    setBusy(a.id); setError(null)
    try {
      const done = await api.resolveAlert(a.id, 'Resolved from the operations dashboard')
      setResolved((xs) => [done, ...xs.filter((x) => x.id !== done.id)])
      onResolved(done)
    } catch (e) {
      setError(`Couldn't resolve ${alertId(a.id)}: ${e}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <div className="card-head">
        <h2>Alerts</h2>
        <span className="hint">{open.length} open{flagged ? ` · ${flagged} need review` : ''}</span>
      </div>
      {error && <p className="error">{error}</p>}
      <ul className="alerts">
        {open.map((a) => {
          const flag = FLAGGED.includes(a.type)
          return (
            <li key={a.id} className={`${a.severity}${flag ? ' flag' : ''}`}>
              <div className="alert-top">
                <span className="alert-id num">{alertId(a.id)}</span>
                <b>{label(a.type)}</b>
                {flag && <span className="pill review">{a.type === 'anomaly' ? 'contradiction' : 'weak evidence'}</span>}
                <span className="pill">{a.status}</span>
                <span className="muted num alert-time">{hhmm(a.created_at)}</span>
              </div>
              <div className="alert-msg">{a.message}</div>
              <div className="alert-actions">
                {a.stay_id && <a href={`#detection/${a.lot_id}/${encodeURIComponent(a.stay_id)}`}>Evidence for {a.stay_id}</a>}
                {!a.stay_id && a.booking_id && <span className="muted">Booking {a.booking_id}</span>}
                {a.estimated_fee != null && <span className="muted">est. fee ${a.estimated_fee.toFixed(2)}</span>}
                <button className="button small" disabled={busy === a.id} onClick={() => resolve(a)}>
                  {busy === a.id ? 'Resolving…' : 'Resolve'}
                </button>
              </div>
            </li>
          )
        })}
        {open.length === 0 && <li className="empty">No open alerts.</li>}
      </ul>
      {resolved.length > 0 && (
        <>
          <button className="linkish resolved-toggle" aria-expanded={showResolved} onClick={() => setShowResolved((v) => !v)}>
            {showResolved ? 'Hide' : 'Show'} {resolved.length} resolved this session
          </button>
          {showResolved && (
            <ul className="alerts resolved">
              {resolved.map((a) => (
                <li key={a.id}>
                  <div className="alert-top">
                    <span className="alert-id num">{alertId(a.id)}</span><b>{label(a.type)}</b>
                    <span className="pill completed">{a.status}</span>
                    <span className="muted num alert-time">{a.resolved_by} · {hhmm(a.resolved_at)}</span>
                  </div>
                  <div className="alert-msg muted">{a.message}</div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  )
}
