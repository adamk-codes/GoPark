import { useEffect, useMemo, useState } from 'react'
import { api, hhmm, isoDate, label, pct, pctFine, TZ, useLots, type EvidenceResult } from './api'
import { base, Chart, useTokens, type Option } from './Chart'
import type { Stay, StayEvidencePoint } from './contract'
import { FUSION } from './fusionConfig'

const POLL_MS = 5000
type Direction = StayEvidencePoint['direction']

/** Stays worth explaining today: anything that arrived, left or is mid-decision on the demo date. */
function isToday(s: Stay, today: string | null) {
  if (!today) return true
  return (s.in_at && isoDate(s.in_at) === today) || (s.out_at && isoDate(s.out_at) === today) || s.state === 'ARRIVING'
}

export function DetectionLog({ now, lot: routeLot, stayId }: { now: string | null; lot: string | null; stayId: string | null }) {
  const lots = useLots()
  const [lot, setLot] = useState(routeLot ?? 'AUB')
  const [stays, setStays] = useState<Stay[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(stayId)
  const [result, setResult] = useState<EvidenceResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const today = now ? isoDate(now) : null

  useEffect(() => { if (routeLot) setLot(routeLot); if (stayId) setSelectedId(stayId) }, [routeLot, stayId])

  useEffect(() => {
    let alive = true
    const load = () => api.stays(lot).then((s) => { if (alive) { setStays(s); setError(null) } })
      .catch((e) => alive && setError(`Can't reach the API: ${e}`))
    load()
    const id = setInterval(load, POLL_MS)
    return () => { alive = false; clearInterval(id) }
  }, [lot])

  const selected = stays.find((s) => s.id === selectedId) ?? null
  // Refetch evidence when the stay changes state or confidence (new signals were fused), not on every poll.
  const stamp = selected ? `${selected.id}|${selected.state}|${selected.in_conf}|${selected.out_conf}` : null
  useEffect(() => {
    if (!selected) { setResult(null); return }
    let alive = true
    api.evidence(selected).then((r) => alive && setResult(r))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp])

  const pickLot = (l: string) => { setLot(l); setSelectedId(null); location.hash = 'detection' }
  const pickStay = (s: Stay) => { setSelectedId(s.id); location.hash = `detection/${s.lot_id}/${encodeURIComponent(s.id)}` }
  const todays = stays.filter((s) => isToday(s, today))

  return (
    <div className="page">
      <div className="toolbar">
        <label className="field" htmlFor="dlot">Lot</label>
        <select id="dlot" value={lot} onChange={(e) => pickLot(e.target.value)}>
          {lots.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.kind})</option>)}
        </select>
        <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>Today's stays. Pick one to see how its arrival and departure were decided.</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="split">
        <section className="card table-wrap" style={{ padding: 0, maxHeight: 720, overflowY: 'auto' }}>
          <table>
            <thead><tr><th>Plate</th><th>In</th><th>Out</th><th>State</th><th>How</th><th className="num">P in</th></tr></thead>
            <tbody>
              {todays.map((s) => (
                <tr key={s.id} className={`clickable${selectedId === s.id ? ' selected' : ''}`} onClick={() => pickStay(s)}
                  tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && pickStay(s)} aria-selected={selectedId === s.id}>
                  <td>{s.plate_norm ?? '—'}<div className="muted" style={{ fontSize: 'var(--fs-xs)' }}>{s.booking_id ?? 'walk-in'}</div></td>
                  <td className="num">{hhmm(s.in_at)}</td>
                  <td className="num">{hhmm(s.out_at)}</td>
                  <td>{label(s.state.toLowerCase())}</td>
                  <td>{s.in_method ? <span className={`pill ${s.in_method}`}>{s.in_method}</span> : '—'}</td>
                  <td className="num">{s.in_conf != null ? pct(s.in_conf) : '—'}</td>
                </tr>
              ))}
              {todays.length === 0 && <tr><td colSpan={6} className="muted">No stays today at this lot yet.</td></tr>}
            </tbody>
          </table>
        </section>
        <section className="card">
          {!selected && <p className="muted">{selectedId ? `Stay ${selectedId} isn't in this lot's list.` : 'Select a stay.'}</p>}
          {selected && !result && <p className="muted">Loading evidence…</p>}
          {selected && result && <EvidenceView stay={selected} result={result} />}
        </section>
      </div>
    </div>
  )
}

function EvidenceView({ stay, result }: { stay: Stay; result: EvidenceResult }) {
  const { ev, mocked, reason } = result
  // Memoised so the 5 s stay poll doesn't hand the charts new arrays (which restarts their animation).
  const arrival = useMemo(() => ev.evidence.filter((e) => e.direction === 'arrival'), [ev])
  const departure = useMemo(() => ev.evidence.filter((e) => e.direction === 'departure'), [ev])
  const priorFromRhythm = Math.abs(ev.prior - FUSION.defaultPrior) > 1e-6

  return (
    <>
      <div className="card-head">
        <h2>{stay.plate_norm ?? stay.id}</h2>
        <span className="hint">{stay.id} · {stay.booking_id ?? 'walk-in'} · {stay.lot_id} · {label(stay.state.toLowerCase())}</span>
        {mocked ? <span className="pill warning">mock data</span> : <span className="pill completed">live evidence</span>}
      </div>
      {mocked && (
        <p className="notice warn">
          <b>Mock evidence.</b> <code>GET /stays/{stay.id}/evidence</code> failed ({reason}), so this panel shows a simulated
          timeline built with the same likelihood ratios. It is not what fusion decided.
        </p>
      )}
      {!mocked && ev.evidence.length === 0 && (
        <p className="notice">
          No fusion evidence recorded for this stay. {stay.in_method === 'attendant' || !stay.booking_id
            ? `It was checked in by ${stay.booking_id ? 'an attendant scan' : 'a walk-in registration'}, so no signals were fused.`
            : 'No driver or camera signals have arrived for it yet.'}
        </p>
      )}
      <Legend />
      <DirectionPanel title="Arrival" dir="arrival" points={arrival} prior={ev.prior}
        priorNote={mocked ? 'simulated' : priorFromRhythm ? 'from the driver’s routine (Rhythm)' : 'default: booking exists, no routine match'}
        outcome={stay.in_at ? `${stay.in_method ?? '—'} at ${hhmm(stay.in_at)}${stay.in_conf != null ? ` · ${pctFine(stay.in_conf)}` : ''}` : null} />
      <DirectionPanel title="Departure" dir="departure" points={departure} prior={FUSION.defaultPrior}
        priorNote="default departure prior"
        outcome={stay.out_at ? `${stay.out_method ?? '—'} at ${hhmm(stay.out_at)}${stay.out_conf != null ? ` · ${pctFine(stay.out_conf)}` : ''}` : null}
        emptyText={stay.out_at ? `Checked out by ${stay.out_method ?? 'unknown'}; no departure signals were fused.` : 'Still parked: departure evidence is recorded once fusion confirms the exit.'} />
    </>
  )
}

function Legend() {
  const t = useTokens()
  return (
    <div className="legend" style={{ marginBottom: 4 }}>
      <span><i style={{ background: t.accent }} />Fused probability after each signal</span>
      <span><i style={{ background: t.warning }} />Ask driver ≥ {pct(FUSION.askDriver)}</span>
      <span><i style={{ background: t.good }} />Auto-confirm ≥ {pct(FUSION.autoConfirm)}</span>
      <span className="muted">from config/fusion.yaml · freshness half-life {FUSION.halfLifeMin} min</span>
    </div>
  )
}

const fmtTime = (ms: number) => new Date(ms).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: TZ })
const signed = (x: number) => (Math.abs(x) < 0.005 ? '≈0' : `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(2)}`)

/** Index of the signal that first lifted the probability to `th` or above (null if it never did). */
function crossing(points: StayEvidencePoint[], prior: number, th: number) {
  let prev = prior
  for (let i = 0; i < points.length; i++) {
    if (prev < th && points[i].probability >= th) return i
    prev = points[i].probability
  }
  return prior >= th && points.length ? -1 : null
}

function DirectionPanel({ title, dir, points, prior, priorNote, outcome, emptyText }: {
  title: string; dir: Direction; points: StayEvidencePoint[]; prior: number; priorNote: string
  outcome: string | null; emptyText?: string
}) {
  const t = useTokens()
  const ask = crossing(points, prior, FUSION.askDriver)
  const auto = crossing(points, prior, FUSION.autoConfirm)
  const sources = useMemo(() => [...new Set(points.map((p) => p.source))], [points])

  const option = useMemo<Option>(() => {
    const b = base(t)
    const times = points.map((p) => Date.parse(p.observed_at))
    const start = Math.min(...times) - 4 * 60000
    const end = Math.max(...times) + 4 * 60000
    const line = [[start, prior, 'prior', 0], ...points.map((p, i) => [times[i], p.probability, `${label(p.source)} ${p.kind}`, p.weighted_log_lr])]
    const timeAxis = { ...b.axis, type: 'time' as const, min: start, max: end, splitLine: { show: false },
      axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => fmtTime(v), hideOverlap: true } }
    return {
      textStyle: b.textStyle,
      grid: [{ left: 52, right: 112, top: 18, height: 150 }, { left: 52, right: 112, top: 196, height: Math.max(24, sources.length * 22) }],
      tooltip: { ...b.tooltip, trigger: 'item',
        formatter: (p: { seriesIndex: number; data: [number, number | string, string, number] }) => p.seriesIndex === 0
          ? `<b>${p.data[2]}</b><br/>${fmtTime(p.data[0])} · P = ${pctFine(p.data[1] as number)}${p.data[2] !== 'prior' ? `<br/>${signed(p.data[3])} log-odds` : ''}`
          : `<b>${label(String(p.data[1]))} ${p.data[2]}</b><br/>${fmtTime(p.data[0])} · ${signed(p.data[3])} log-odds` },
      xAxis: [{ ...timeAxis, gridIndex: 0, axisLabel: { show: false } }, { ...timeAxis, gridIndex: 1 }],
      yAxis: [
        { ...b.axis, gridIndex: 0, type: 'value', min: 0, max: 1, interval: 0.25, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => pct(v) } },
        { ...b.axis, gridIndex: 1, type: 'category', data: sources, inverse: true, splitLine: { show: true, lineStyle: { color: t.line } },
          axisLabel: { ...b.axis.axisLabel, formatter: (v: string) => label(v) } },
      ],
      series: [
        {
          name: `P(${dir})`, type: 'line', step: 'end', xAxisIndex: 0, yAxisIndex: 0, data: line, symbol: 'circle', symbolSize: 8,
          lineStyle: { color: t.accent, width: 2 }, itemStyle: { color: t.accent, borderColor: t.surface, borderWidth: 2 },
          markLine: { silent: true, symbol: 'none', lineStyle: { type: 'dashed', width: 1.5 }, label: { position: 'end', fontSize: 11 },
            // Explicit start/end coords: a bare `yAxis` guide can end mid-plot on a sparse time axis.
            data: [
              [{ coord: [start, FUSION.autoConfirm], lineStyle: { color: t.good }, label: { color: t.good, formatter: `auto-confirm ${pct(FUSION.autoConfirm)}` } },
                { coord: [end, FUSION.autoConfirm] }],
              [{ coord: [start, FUSION.askDriver], lineStyle: { color: t.warning }, label: { color: t.warning, formatter: `ask driver ${pct(FUSION.askDriver)}` } },
                { coord: [end, FUSION.askDriver] }],
            ] },
          markArea: { silent: true, data: [
            [{ yAxis: FUSION.autoConfirm, itemStyle: { color: t.good, opacity: 0.08 } }, { yAxis: 1 }],
            [{ yAxis: FUSION.askDriver, itemStyle: { color: t.warning, opacity: 0.07 } }, { yAxis: FUSION.autoConfirm }]] },
        },
        {
          name: 'Signals', type: 'scatter', xAxisIndex: 1, yAxisIndex: 1, symbolSize: 11,
          data: points.map((p, i) => ({ value: [times[i], p.source, p.kind, p.weighted_log_lr],
            itemStyle: { color: p.weighted_log_lr >= 0 ? t.accent : t.critical, borderColor: t.surface, borderWidth: 1.5 } })),
        },
      ],
    }
  }, [t, points, prior, dir, sources])

  return (
    <div className="direction">
      <div className="card-head">
        <h3>{title}</h3>
        <span className="hint">prior {pct(prior)} · {priorNote}</span>
        {outcome && <span className="pill">{outcome}</span>}
        {ask != null && points.length > 0 && <span className="pill warning">{ask < 0 ? `≥ ${pct(FUSION.askDriver)} from the prior` : `crossed ${pct(FUSION.askDriver)} at ${hhmm(points[ask].observed_at)}`}</span>}
        {auto != null && points.length > 0 && <span className="pill auto">{auto < 0 ? `≥ ${pct(FUSION.autoConfirm)} from the prior` : `crossed ${pct(FUSION.autoConfirm)} at ${hhmm(points[auto].observed_at)}`}</span>}
      </div>
      {points.length === 0 ? <p className="muted empty-dir">{emptyText ?? 'No signals fused for this direction.'}</p> : (
        <>
          <Chart option={option} className="chart evidence" style={{ height: 196 + Math.max(24, sources.length * 22) + 34 }}
            label={`${title} probability after each signal against the ${pct(FUSION.askDriver)} and ${pct(FUSION.autoConfirm)} thresholds, with a lane per signal source`} />
          <ul className="steps">
            <li className="steps-head"><span>time</span><span>signal</span><span className="lr">log-LR · after freshness → P</span></li>
            <li><span className="t">start</span><span>Prior <span className="muted">{priorNote}</span></span><span className="lr">{pctFine(prior)}</span></li>
            {points.map((s, i) => (
              <li key={s.signal_id} className={i === auto ? 'cross-auto' : i === ask ? 'cross-ask' : undefined}>
                <span className="t">{hhmm(s.observed_at)}</span>
                <span>{label(s.source)} <span className="muted">{s.kind}</span>
                  {i === ask && <span className="pill warning">≥ {pct(FUSION.askDriver)}</span>}
                  {i === auto && <span className="pill auto">≥ {pct(FUSION.autoConfirm)}</span>}
                </span>
                <span className="lr">
                  {Math.abs(s.log_lr) >= 20 ? 'definitive' : <>{signed(s.log_lr)}{Math.abs(s.weighted_log_lr - s.log_lr) > 0.005 && <> · {signed(s.weighted_log_lr)}</>}</>}
                  {' → '}<b>{pctFine(s.probability)}</b>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
