import { useEffect, useState } from 'react'
import { api, hhmm, pct, type Alert } from './api'
import type { AuditEntry, Lot, LotOverview, Stay } from './contract'

const POLL_MS = 5000

export function LiveLots() {
  const [lots, setLots] = useState<Lot[]>([])
  const [lot, setLot] = useState('AUB')
  const [ov, setOv] = useState<LotOverview | null>(null)
  const [stays, setStays] = useState<Stay[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.lots().then(setLots).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    let alive = true
    const load = () =>
      Promise.all([api.overview(lot), api.stays(lot), api.alerts(lot), api.audit(lot)])
        .then(([o, s, a, l]) => {
          if (!alive) return
          setOv(o); setStays(s); setAlerts(a); setAudit(l); setError(null)
        })
        .catch((e) => alive && setError(`Can't reach the API: ${e}`))
    load()
    const id = setInterval(load, POLL_MS)
    return () => { alive = false; clearInterval(id) }
  }, [lot])

  return (
    <div className="page">
      <div className="toolbar">
        <label htmlFor="lot">Lot</label>
        <select id="lot" value={lot} onChange={(e) => setLot(e.target.value)}>
          {lots.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.kind})</option>)}
        </select>
        {ov && <span className="muted">Demo clock {hhmm(ov.now)}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      {ov && (
        <>
          <div className="tiles">
            <Tile label="Occupied" value={ov.counts.occupied} of={ov.counts.capacity} tone="occupied" />
            <Tile label="Reserved (next hour)" value={ov.counts.reserved} tone="reserved" />
            <Tile label="Free" value={ov.counts.free} tone="free" />
          </div>
          {ov.spaces && (
            <div className="spaces" aria-label="Spot map">
              {ov.spaces.map((s) => <span key={s.id} className={`space ${s.state}`} title={`${s.label}: ${s.state}`}>{s.label}</span>)}
            </div>
          )}
          <div className="grid2">
            <section>
              <h2>Expected in the next 30 min</h2>
              <table>
                <thead><tr><th>Time</th><th>Driver</th><th>Plate</th><th>Likelihood</th><th>From</th></tr></thead>
                <tbody>
                  {ov.expected_soon.map((e) => (
                    <tr key={`${e.user_id}-${e.at}`}>
                      <td>{hhmm(e.at)}</td><td>{e.user_id}</td><td>{e.plate ?? '—'}</td><td>{pct(e.p)}</td>
                      <td><span className={`pill ${e.source}`}>{e.source === 'prediction' ? 'habit, no booking' : 'booking'}</span></td>
                    </tr>
                  ))}
                  {ov.expected_soon.length === 0 && <tr><td colSpan={5} className="muted">Nobody expected.</td></tr>}
                </tbody>
              </table>
            </section>
            <section>
              <h2>Alerts</h2>
              <ul className="alerts">
                {alerts.map((a, i) => <li key={i} className={a.severity}><b>{a.type.replace('_', ' ')}</b> {a.message}</li>)}
                {alerts.length === 0 && <li className="muted">No alerts.</li>}
              </ul>
              <h2>Recent activity</h2>
              <ul className="audit">
                {audit.map((a) => <li key={a.id}>{hhmm(a.observed_at)} · {a.action.replace('_', ' ')} · {a.entity_id} <span className="muted">({a.device_id})</span></li>)}
                {audit.length === 0 && <li className="muted">No activity yet.</li>}
              </ul>
            </section>
          </div>
          <section>
            <h2>Parked now ({stays.length})</h2>
            <table>
              <thead><tr><th>Plate</th><th>Booking</th><th>In at</th><th>How</th><th>Confidence</th></tr></thead>
              <tbody>
                {stays.map((s) => (
                  <tr key={s.id}>
                    <td>{s.plate_norm}</td><td>{s.booking_id}</td><td>{hhmm(s.in_at)}</td>
                    <td><span className={`pill ${s.in_method}`}>{s.in_method}</span></td>
                    <td>{s.in_conf != null ? pct(s.in_conf) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  )
}

function Tile({ label, value, of, tone }: { label: string; value: number; of?: number; tone: string }) {
  return (
    <div className={`tile ${tone}`}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}{of != null && <small> / {of}</small>}</span>
    </div>
  )
}
