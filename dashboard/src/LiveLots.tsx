import { useEffect, useMemo, useState } from 'react'
import { api, hhmm, isoDate, label, pct, type Alert } from './api'
import { base, Chart, useTokens, type Option } from './Chart'
import type { AuditEntry, Lot, LotForecast, LotOverview, Method, Stay } from './contract'

const POLL_MS = 5000
const METHODS: Method[] = ['auto', 'driver', 'attendant', 'inferred']
const HOURS = Array.from({ length: 19 }, (_, i) => i + 5) // 05:00–23:00

export function LiveLots() {
  const [lots, setLots] = useState<Lot[]>([])
  const [lot, setLot] = useState('AUB')
  const [ov, setOv] = useState<LotOverview | null>(null)
  const [stays, setStays] = useState<Stay[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [forecast, setForecast] = useState<LotForecast[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const today = ov ? isoDate(ov.now) : null

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

  useEffect(() => {
    if (!today) return
    let alive = true
    setForecast(null)
    api.forecast(lot, today).then((f) => alive && setForecast(f)).catch(() => alive && setForecast([]))
    return () => { alive = false }
  }, [lot, today])

  const parked = stays.filter((s) => s.state === 'CHECKED_IN' || s.state === 'OVERSTAY')
  const todays = today ? stays.filter((s) => s.in_at && isoDate(s.in_at) === today) : []
  const byMethod = METHODS.map((m) => [m, todays.filter((s) => s.in_method === m).length] as const)

  return (
    <div className="page">
      <div className="toolbar">
        <label className="field" htmlFor="lot">Lot</label>
        <select id="lot" value={lot} onChange={(e) => setLot(e.target.value)}>
          {lots.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.kind})</option>)}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      {ov && (
        <>
          <div className="tiles">
            <Tile label="Occupied" value={ov.counts.occupied} of={ov.counts.capacity} tone="occupied" />
            <Tile label="Reserved (next hour)" value={ov.counts.reserved} tone="reserved" />
            <Tile label="Free" value={ov.counts.free} tone="free" />
            <div className="tile accent">
              <span className="tile-label">Check-ins today by method</span>
              <span className="tile-value">{todays.length}</span>
              <span className="tile-sub">
                {byMethod.filter(([, n]) => n).map(([m, n]) => <span key={m} className={`pill ${m}`}>{m} {n}</span>)}
                {todays.length === 0 && 'none yet'}
              </span>
            </div>
          </div>
          {ov.spaces && (
            <div className="spaces" aria-label="Spot map">
              {ov.spaces.map((s) => <span key={s.id} className={`space ${s.state}`} title={`${s.label}: ${s.state}`}>{s.label}</span>)}
            </div>
          )}

          <ForecastCard forecast={forecast} stays={todays} now={ov.now} />

          <div className="grid2">
            <section className="card">
              <div className="card-head"><h2>Expected in the next 30 min</h2></div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Time</th><th>Driver</th><th>Plate</th><th className="num">Likelihood</th><th>From</th></tr></thead>
                  <tbody>
                    {ov.expected_soon.map((e) => (
                      <tr key={`${e.user_id}-${e.at}`}>
                        <td className="num">{hhmm(e.at)}</td>
                        <td><a href={`#user/${e.user_id}`}>{e.user_id}</a></td>
                        <td>{e.plate ?? '—'}</td><td className="num">{pct(e.p)}</td>
                        <td><span className={`pill ${e.source}`}>{e.source === 'prediction' ? 'habit, no booking' : 'booking'}</span></td>
                      </tr>
                    ))}
                    {ov.expected_soon.length === 0 && <tr><td colSpan={5} className="muted">Nobody expected.</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="card">
              <div className="card-head"><h2>Alerts</h2></div>
              <ul className="alerts">
                {alerts.map((a, i) => <li key={i} className={a.severity}><b>{label(a.type)}</b> {a.message}</li>)}
                {alerts.length === 0 && <li className="empty">No alerts.</li>}
              </ul>
              <h2>Recent activity</h2>
              <ul className="audit">
                {audit.map((a) => <li key={a.id}><span className="num muted">{hhmm(a.observed_at)}</span> · {label(a.action)} · {a.entity_id} <span className="muted">({a.device_id})</span></li>)}
                {audit.length === 0 && <li className="empty">No activity yet.</li>}
              </ul>
            </section>
          </div>
          <section className="card" style={{ marginTop: 16 }}>
            <div className="card-head"><h2>Parked now ({parked.length})</h2><span className="hint">How each car was checked in</span></div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Plate</th><th>Booking</th><th>In at</th><th>How</th><th className="num">Confidence</th></tr></thead>
                <tbody>
                  {parked.map((s) => (
                    <tr key={s.id}>
                      <td>{s.plate_norm}</td><td>{s.booking_id}</td><td className="num">{hhmm(s.in_at)}</td>
                      <td>{s.in_method ? <span className={`pill ${s.in_method}`}>{s.in_method}</span> : '—'}</td>
                      <td className="num">{s.in_conf != null ? pct(s.in_conf) : '—'}</td>
                    </tr>
                  ))}
                  {parked.length === 0 && <tr><td colSpan={5} className="muted">No cars parked.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function ForecastCard({ forecast, stays, now }: { forecast: LotForecast[] | null; stays: Stay[]; now: string }) {
  const t = useTokens()
  const nowHour = +now.slice(11, 13)
  const option = useMemo<Option>(() => {
    const b = base(t)
    const expected = HOURS.map((h) => forecast?.find((f) => f.hour === h)?.expected_arrivals ?? 0)
    const actual = HOURS.map((h) => (h <= nowHour ? stays.filter((s) => s.in_at && +s.in_at.slice(11, 13) === h).length : null))
    return {
      textStyle: b.textStyle,
      grid: { left: 36, right: 16, top: 16, bottom: 28 },
      tooltip: { ...b.tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: t['line-strong'] } },
        valueFormatter: (v: unknown) => (v == null ? '—' : typeof v === 'number' ? (Math.round(v * 10) / 10).toString() : String(v)) },
      xAxis: { ...b.axis, type: 'category', data: HOURS.map((h) => `${String(h).padStart(2, '0')}:00`), splitLine: { show: false } },
      yAxis: { ...b.axis, type: 'value', minInterval: 1 },
      series: [
        { name: 'Checked in', type: 'bar', data: actual, barMaxWidth: 18, itemStyle: { color: t['series-actual'], borderRadius: [4, 4, 0, 0] } },
        { name: 'Forecast', type: 'line', data: expected, smooth: 0.25, smoothMonotone: 'x', symbol: 'circle', symbolSize: 8, showSymbol: false,
          lineStyle: { color: t['series-forecast'], width: 2 }, itemStyle: { color: t['series-forecast'], borderColor: t.surface, borderWidth: 2 },
          markLine: { silent: true, symbol: 'none', label: { formatter: 'now', color: t.muted, fontSize: 11 },
            lineStyle: { color: t.faint, type: 'dashed', width: 1 }, data: [{ xAxis: `${String(nowHour).padStart(2, '0')}:00` }] } },
      ],
    }
  }, [t, forecast, stays, nowHour])

  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h2>Expected arrivals vs actual check-ins</h2>
        <span className="hint">Cars per hour today · forecast from the latest Rhythm run</span>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="legend">
          <span><i style={{ background: t['series-forecast'] }} />Forecast</span>
          <span><i className="box" style={{ background: t['series-actual'] }} />Checked in</span>
        </span>
      </div>
      {forecast && forecast.length === 0 && <p className="notice">No forecast for today in the latest Rhythm run.</p>}
      <Chart option={option} label="Expected arrivals per hour (line) against actual check-ins per hour (bars) for today" />
    </section>
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
