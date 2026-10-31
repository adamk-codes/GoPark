import { useEffect, useMemo, useState } from 'react'
import { api, dayLabel, dayTime, label, openUser, pct } from './api'
import { base, Chart, useTokens, type Option, type Tokens } from './Chart'
import type { Booking, BookingStatus, PatternRow, UserRhythm } from './contract'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const hourOf = (iso: string) => +iso.slice(11, 13) + +iso.slice(14, 16) / 60
const toMin = (hhmm: string) => +hhmm.slice(0, 2) * 60 + +hhmm.slice(3, 5)
const fmtHour = (h: number) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`

export function UserPage({ userId }: { userId: string | null }) {
  const [data, setData] = useState<UserRhythm | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [input, setInput] = useState(userId ?? '')

  useEffect(() => {
    setInput(userId ?? '')
    if (!userId) return
    let alive = true
    setData(null); setError(null)
    api.user(userId).then((d) => alive && setData(d)).catch((e) => alive && setError(`Couldn't load ${userId}: ${e}`))
    return () => { alive = false }
  }, [userId])

  const primary = data?.patterns.find((p) => p.is_primary) ?? data?.patterns[0] ?? null
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const b of data?.history ?? []) c[b.status] = (c[b.status] ?? 0) + 1
    return c
  }, [data])

  return (
    <div className="page">
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); if (input.trim()) openUser(input.trim().toUpperCase()) }}>
        <label className="field" htmlFor="uid">User</label>
        <input id="uid" placeholder="U0001" value={input} onChange={(e) => setInput(e.target.value)} style={{ width: 110 }} />
        <button className="button primary" type="submit">Open</button>
        <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>Or click a user in the pattern table.</span>
      </form>
      {!userId && <p className="notice">Pick a user to see their weekly rhythm, booking history and next prediction.</p>}
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <div className="card-head" style={{ marginBottom: 12 }}>
            <h1>{data.user_id}</h1>
            {primary && <span className="muted">{primary.label}</span>}
            {primary?.state && <span className={`pill ${primary.state}`}>{primary.state}</span>}
            <span className="hint">
              {data.history.length} bookings · {counts.completed ?? 0} completed · {counts.cancelled ?? 0} cancelled · {counts.no_show ?? 0} no-show · {counts.booked ?? 0} upcoming
            </span>
          </div>
          <div className="grid2" style={{ marginTop: 0 }}>
            <section className="card">
              <div className="card-head"><h2>Booking timeline</h2><span className="hint">Start time of every booking · bands are the routines Rhythm found</span></div>
              <Timeline history={data.history} patterns={data.patterns} />
            </section>
            <Prediction primary={primary} patterns={data.patterns} />
          </div>
          <section className="card" style={{ marginTop: 16 }}>
            <div className="card-head"><h2>Weekly rhythm</h2><span className="hint">Completed bookings by weekday and start hour</span></div>
            <Heatmap heatmap={data.heatmap} />
          </section>
        </>
      )}
    </div>
  )
}

function Prediction({ primary, patterns }: { primary: PatternRow | null; patterns: PatternRow[] }) {
  if (!primary) return <section className="card"><h2>Next prediction</h2><p className="muted">No pattern for this user in the latest run.</p></section>
  return (
    <section className="card">
      <div className="card-head"><h2>Next prediction</h2></div>
      <p className="prediction">
        {primary.next_booking ? `${dayTime(primary.next_booking.at)} · ${primary.next_booking.location_id}` : 'Not predictable'}
      </p>
      <dl className="kv">
        <dt>Confidence</dt><dd className="num"><div className="bar"><span style={{ width: pct(primary.confidence) }} /></div>{pct(primary.confidence)}</dd>
        <dt>Reminder</dt><dd><span className={`pill ${primary.reminder.tier}`}>{label(primary.reminder.tier)}</span>
          {primary.reminder.recommend && primary.reminder.at && <span className="muted"> send {dayTime(primary.reminder.at)}</span>}</dd>
        <dt>Why</dt><dd>{primary.reason}</dd>
        <dt>Type</dt><dd>{label(primary.pattern_type)} · {primary.support} bookings over {primary.active_weeks} weeks · {primary.frequency_per_week.toFixed(1)}/week</dd>
        <dt>Cancel rate</dt><dd className="num">{pct(primary.cancel_rate)}</dd>
        {primary.change_date && <><dt>Changed on</dt><dd>{dayLabel(primary.change_date)}</dd></>}
        {primary.last_booking_date && <><dt>Last booking</dt><dd>{dayLabel(primary.last_booking_date)}{primary.inactivity_detected && <> <span className="pill at_risk">inactive</span></>}</dd></>}
      </dl>
      {patterns.length > 1 && (
        <>
          <h3 style={{ marginTop: 16 }}>All routines</h3>
          <div className="pattern-list">
            {patterns.map((p) => (
              <div key={p.pattern_id} className={`pattern-item${p.is_primary ? ' primary' : ''}`}>
                <b>{p.label}</b> {p.state && <span className={`pill ${p.state}`}>{p.state}</span>}
                <div className="muted" style={{ fontSize: 'var(--fs-sm)' }}>
                  {p.time_window ? `${p.time_window.center} ±${p.time_window.spread_min}′` : 'variable time'} · {p.location_id ?? 'any lot'} · {pct(p.confidence)}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

const STATUS_STYLE: Record<BookingStatus, { name: string; symbol: string; size: number; color: (t: Tokens) => string }> = {
  completed: { name: 'Completed', symbol: 'circle', size: 8, color: (t) => t['series-actual'] },
  cancelled: { name: 'Cancelled', symbol: 'circle', size: 8, color: (t) => t.faint }, // drawn hollow below
  no_show: { name: 'No-show', symbol: 'triangle', size: 10, color: (t) => t.critical },
  booked: { name: 'Upcoming', symbol: 'diamond', size: 11, color: (t) => t['series-forecast'] },
}

function Timeline({ history, patterns }: { history: Booking[]; patterns: PatternRow[] }) {
  const t = useTokens()
  const option = useMemo<Option>(() => {
    const b = base(t)
    const xs = history.map((h) => Date.parse(h.start_time))
    const [minX, maxX] = [Math.min(...xs), Math.max(...xs)]
    const hours = [...history.map((h) => hourOf(h.start_time)),
      ...patterns.filter((p) => p.time_window).map((p) => toMin(p.time_window!.center) / 60)]
    const pad = 3 * 86400000
    const bands = patterns.filter((p) => p.time_window).map((p) => {
      const c = toMin(p.time_window!.center) / 60, s = Math.max(15, p.time_window!.spread_min) / 60
      const from = p.change_date ? Date.parse(p.change_date) : minX - pad
      const to = p.inactivity_detected && p.last_booking_date ? Date.parse(p.last_booking_date) + 86400000 : maxX + pad
      return [{ name: `${p.label}${p.location_id ? ` · ${p.location_id}` : ''}`, xAxis: from, yAxis: c - s }, { xAxis: to, yAxis: c + s }]
    })
    const changes = patterns.filter((p) => p.change_date).map((p) => ({ xAxis: Date.parse(p.change_date!), name: 'change' }))
    const lastSeen = patterns.filter((p) => p.inactivity_detected && p.last_booking_date)
      .map((p) => ({ xAxis: Date.parse(p.last_booking_date!), name: 'last seen' }))
    const statuses = (Object.keys(STATUS_STYLE) as BookingStatus[]).filter((s) => history.some((h) => h.status === s))
    return {
      textStyle: b.textStyle,
      grid: { left: 44, right: 16, top: 34, bottom: 52 },
      legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: t.muted, fontSize: 12 } },
      tooltip: { ...b.tooltip, trigger: 'item',
        formatter: (p: { data: [number, number, string, string, string] }) =>
          `<b>${dayTime(p.data[4])}</b><br/>${p.data[3]} · ${p.data[2]}` },
      dataZoom: [{ type: 'inside', xAxisIndex: 0 }, { type: 'slider', xAxisIndex: 0, height: 16, bottom: 8, borderColor: t.line,
        fillerColor: 'rgba(22,139,88,.12)', handleStyle: { color: t.surface, borderColor: t['line-strong'] }, textStyle: { color: t.muted },
        dataBackground: { lineStyle: { color: t.line }, areaStyle: { color: t['surface-2'] } } }],
      xAxis: { ...b.axis, type: 'time', min: minX - pad, max: maxX + pad, splitLine: { show: false },
        axisLabel: { ...b.axis.axisLabel, formatter: '{d} {MMM}' } },
      yAxis: { ...b.axis, type: 'value', inverse: true, min: Math.max(0, Math.floor(Math.min(...hours) - 1.5)),
        max: Math.min(24, Math.ceil(Math.max(...hours) + 1.5)), minInterval: 1, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => fmtHour(v) } },
      series: statuses.map((s, i) => {
        const st = STATUS_STYLE[s]
        return {
          name: st.name, type: 'scatter', symbol: st.symbol, symbolSize: st.size,
          itemStyle: s === 'cancelled'
            ? { color: t.surface, borderColor: st.color(t), borderWidth: 1.5 }
            : { color: st.color(t), borderColor: t.surface, borderWidth: 1 },
          emphasis: { scale: 1.6 },
          data: history.filter((h) => h.status === s)
            .map((h) => [Date.parse(h.start_time), hourOf(h.start_time), h.location_id, st.name, h.start_time]),
          ...(i === 0 ? {
            markArea: { silent: true, itemStyle: { color: t.accent, opacity: 0.1 },
              label: { color: t.muted, fontSize: 11, position: 'insideTopLeft' }, data: bands },
            markLine: { silent: true, symbol: 'none', lineStyle: { color: t.fg, type: 'dashed', width: 1 },
              label: { formatter: '{b}', color: t.fg, fontSize: 11 }, data: [...changes, ...lastSeen] },
          } : {}),
        }
      }),
    }
  }, [t, history, patterns])
  if (!history.length) return <p className="muted">No bookings.</p>
  return <Chart option={option} className="chart tall" label="Scatter of every booking's date against its start time, by status, with routine bands" />
}

function Heatmap({ heatmap }: { heatmap: number[][] }) {
  const t = useTokens()
  const option = useMemo<Option>(() => {
    const b = base(t)
    const data = heatmap.flatMap((row, d) => row.map((n, h) => [h, d, n]))
    const max = Math.max(1, ...data.map((x) => x[2]))
    return {
      textStyle: b.textStyle,
      grid: { left: 44, right: 16, top: 8, bottom: 56 },
      tooltip: { ...b.tooltip, formatter: (p: { data: number[] }) => `${DAYS[p.data[1]]} ${fmtHour(p.data[0])}–${fmtHour(p.data[0] + 1)}<br/><b>${p.data[2]}</b> completed` },
      xAxis: { ...b.axis, type: 'category', data: Array.from({ length: 24 }, (_, h) => fmtHour(h)), splitLine: { show: false } },
      yAxis: { ...b.axis, type: 'category', data: DAYS, inverse: true, splitLine: { show: false } },
      visualMap: { min: 0, max, orient: 'horizontal', left: 'center', bottom: 0, itemHeight: 140, itemWidth: 10, calculable: false,
        text: [String(max), '0'], textStyle: { color: t.muted, fontSize: 11 },
        inRange: { color: [t['seq-0'], t['seq-1'], t['seq-2'], t['seq-3'], t['seq-4']] } },
      series: [{ type: 'heatmap', data, itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 3 },
        emphasis: { itemStyle: { borderColor: t.fg, borderWidth: 1 } } }],
    }
  }, [t, heatmap])
  return <Chart option={option} className="chart short" label="Heatmap of completed bookings by weekday and start hour" />
}
