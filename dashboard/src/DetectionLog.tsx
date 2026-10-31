import { useEffect, useMemo, useState } from 'react'
import { api, hhmm, isoDate, label, pct, pctFine, type StayEvidence } from './api'
import { base, Chart, useTokens, type Option } from './Chart'
import type { Lot, Stay } from './contract'

export function DetectionLog({ now }: { now: string | null }) {
  const [lots, setLots] = useState<Lot[]>([])
  const [lot, setLot] = useState('AUB')
  const [stays, setStays] = useState<Stay[]>([])
  const [selected, setSelected] = useState<Stay | null>(null)
  const [evidence, setEvidence] = useState<StayEvidence | null>(null)
  const [error, setError] = useState<string | null>(null)
  const today = now ? isoDate(now) : null

  useEffect(() => { api.lots().then(setLots).catch((e) => setError(String(e))) }, [])

  useEffect(() => {
    let alive = true
    const load = () => api.stays(lot).then((s) => alive && setStays(s)).catch((e) => alive && setError(`Can't reach the API: ${e}`))
    load()
    const id = setInterval(load, 10000)
    return () => { alive = false; clearInterval(id) }
  }, [lot])

  useEffect(() => { setSelected(null); setEvidence(null) }, [lot])

  useEffect(() => {
    if (!selected) return
    let alive = true
    api.evidence(selected).then((e) => alive && setEvidence(e))
    return () => { alive = false }
  }, [selected])

  const todays = stays.filter((s) => !today || (s.in_at && isoDate(s.in_at) === today))

  return (
    <div className="page">
      <div className="toolbar">
        <label className="field" htmlFor="dlot">Lot</label>
        <select id="dlot" value={lot} onChange={(e) => setLot(e.target.value)}>
          {lots.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.kind})</option>)}
        </select>
        <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>Today's stays. Pick one to see how its check-in was decided.</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="split">
        <section className="card table-wrap" style={{ padding: 0, maxHeight: 640, overflowY: 'auto' }}>
          <table>
            <thead><tr><th>Plate</th><th>In</th><th>State</th><th>How</th><th className="num">P</th></tr></thead>
            <tbody>
              {todays.map((s) => (
                <tr key={s.id} className={`clickable${selected?.id === s.id ? ' selected' : ''}`} onClick={() => setSelected(s)}>
                  <td>{s.plate_norm ?? '—'}<div className="muted" style={{ fontSize: 'var(--fs-xs)' }}>{s.booking_id ?? 'walk-in'}</div></td>
                  <td className="num">{hhmm(s.in_at)}</td>
                  <td>{label(s.state.toLowerCase())}</td>
                  <td>{s.in_method ? <span className={`pill ${s.in_method}`}>{s.in_method}</span> : '—'}</td>
                  <td className="num">{s.in_conf != null ? pct(s.in_conf) : '—'}</td>
                </tr>
              ))}
              {todays.length === 0 && <tr><td colSpan={5} className="muted">No stays today at this lot yet.</td></tr>}
            </tbody>
          </table>
        </section>
        <section className="card">
          {!selected && <p className="muted">Select a stay.</p>}
          {selected && evidence && <EvidenceView stay={selected} ev={evidence} />}
        </section>
      </div>
    </div>
  )
}

function EvidenceView({ stay, ev }: { stay: Stay; ev: StayEvidence }) {
  const t = useTokens()
  const option = useMemo<Option>(() => {
    const b = base(t)
    const first = ev.timeline[0] ? Date.parse(ev.timeline[0].at) : Date.parse(stay.in_at ?? '')
    const start = first - 5 * 60000
    const points = [[start, ev.prior, 'prior'], ...ev.timeline.map((s) => [Date.parse(s.at), s.p_after, `${label(s.source)} ${s.kind}`])]
    const { auto_confirm: hi, ask_driver: lo } = ev.thresholds
    return {
      textStyle: b.textStyle,
      grid: { left: 44, right: 96, top: 30, bottom: 28 },
      tooltip: { ...b.tooltip, trigger: 'item',
        formatter: (p: { data: [number, number, string] }) =>
          `<b>${p.data[2]}</b><br/>${new Date(p.data[0]).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Beirut' })} · P = ${pctFine(p.data[1])}` },
      xAxis: { ...b.axis, type: 'time', min: start - 60000, max: Math.max(...points.map((p) => p[0] as number)) + 3 * 60000, splitLine: { show: false },
        axisLabel: { ...b.axis.axisLabel, formatter: '{HH}:{mm}' } },
      yAxis: { ...b.axis, type: 'value', min: 0, max: 1, interval: 0.2, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => pct(v) } },
      series: [{
        name: 'Fused P(arrived)', type: 'line', step: 'end', data: points, symbol: 'circle', symbolSize: 9,
        lineStyle: { color: t.accent, width: 2 }, itemStyle: { color: t.accent, borderColor: t.surface, borderWidth: 2 },
        label: { show: true, position: 'top', formatter: (p: { data: [number, number, string] }) => p.data[2], color: t.muted, fontSize: 11 },
        labelLayout: { hideOverlap: true },
        markLine: { silent: true, symbol: 'none', lineStyle: { type: 'dashed', width: 1 },
          label: { position: 'end', fontSize: 11, color: t.muted },
          data: [
            { yAxis: hi, name: `auto ${pct(hi)}`, lineStyle: { color: t.good }, label: { formatter: `auto ${pct(hi)}` } },
            { yAxis: lo, name: `ask ${pct(lo)}`, lineStyle: { color: t.warning }, label: { formatter: `ask driver ${pct(lo)}` } },
          ] },
        markArea: { silent: true, data: [[{ yAxis: hi, itemStyle: { color: t.good, opacity: 0.06 } }, { yAxis: 1 }],
          [{ yAxis: lo, itemStyle: { color: t.warning, opacity: 0.06 } }, { yAxis: hi }]] },
      }],
    }
  }, [t, ev, stay.in_at])

  return (
    <>
      <div className="card-head">
        <h2>{stay.plate_norm ?? stay.id}</h2>
        <span className="hint">{stay.booking_id ?? 'walk-in'} · {ev.lot_id} · prior {pct(ev.prior)} ({ev.prior_source})</span>
        {ev.p != null && <span className={`pill ${ev.p >= ev.thresholds.auto_confirm ? 'auto' : ev.p >= ev.thresholds.ask_driver ? 'driver' : 'attendant'}`}>P = {pctFine(ev.p)}</span>}
      </div>
      {ev.mocked && <p className="notice">Mock evidence: fusion steps aren't recorded yet (P2 phase P2). Shape matches <code>GET /stays/{'{id}'}/evidence</code>.</p>}
      <Chart option={option} label="Fused arrival confidence after each signal, with the 60% ask-driver and 95% auto check-in thresholds" />
      <ul className="steps">
        <li><span className="t">start</span><span>Prior from {ev.prior_source === 'rhythm' ? 'the driver’s routine (Rhythm)' : ev.prior_source === 'default' ? 'the booking' : 'nothing (walk-in)'}</span><span className="lr">{pct(ev.prior)}</span></li>
        {ev.timeline.map((s, i) => (
          <li key={s.client_event_id ?? i}>
            <span className="t">{hhmm(s.at)}</span>
            <span>{label(s.source)} <span className="muted">{s.kind}</span>{s.phase === 'departure' && <span className="pill">departure</span>}</span>
            <span className="lr">{s.definitive ? 'definitive' : s.contribution != null ? `+${s.contribution.toFixed(2)} log-odds` : ''} → <b>{pctFine(s.p_after)}</b></span>
          </li>
        ))}
      </ul>
    </>
  )
}
