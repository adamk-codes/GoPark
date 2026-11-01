import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, label, pct } from './api'
import { base, Chart, useTokens, type Option, type Tokens } from './Chart'
import {
  ABLATION_KEYS, asTable, findKey, firstTable, fmtMetric, HIDDEN_KEYS, isObj, KNOWN_KEYS, readCalibration, readReminders,
  type FusionMetrics, type FusionScore, type ModelScore, type Reliability, type ReminderCurve, type RhythmMetrics, type Table,
} from './metrics'

const ENGINE = 'engine'
const modelName = (m: string) => (m === ENGINE ? 'Rhythm engine' : m)

/** Engine in the brand colour, baselines in neutral grey: one thing to look at, not a categorical palette. */
const modelColor = (t: Tokens, m: string) => (m === ENGINE ? t.accent : t.faint)

export function Evaluation() {
  const [metrics, setMetrics] = useState<RhythmMetrics | null>(null)
  const [fusion, setFusion] = useState<FusionMetrics | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [split, setSplit] = useState('test')

  useEffect(() => {
    api.evaluation().then(setMetrics).catch((e) => setError(`No evaluation for the latest run yet (${e}). Run \`uv run python -m rhythm evaluate\`.`))
    api.fusionMetrics().then(setFusion)
  }, [])

  const splits = metrics?.next_booking ? Object.keys(metrics.next_booking) : []
  const s = splits.includes(split) ? split : splits[0]
  const extra = metrics ? Object.entries(metrics).filter(([k, v]) => !KNOWN_KEYS.has(k) && firstTable(v)) : []
  const ablation = metrics ? findKey(metrics, ABLATION_KEYS, 1) : undefined
  const hidden = metrics ? findKey(metrics, HIDDEN_KEYS, 2) : undefined
  const embeddedFusion = metrics ? findKey(metrics, ['fusion', 'detection'], 0) as FusionMetrics | undefined : undefined

  return (
    <div className="page">
      <div className="toolbar">
        <h1>Evaluation</h1>
        {metrics?.run_id && <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>run {metrics.run_id}{metrics.end ? ` · data to ${metrics.end}` : ''}</span>}
        <span className="spacer" />
        {splits.length > 1 && (
          <div className="segmented" role="group" aria-label="Backtest split">
            {splits.map((x) => <button key={x} className={x === s ? 'on' : ''} aria-pressed={x === s} onClick={() => setSplit(x)}>{label(x)} weeks</button>)}
          </div>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {metrics && (
        <>
          <h2 className="section">Booking patterns (C2): Rhythm vs baselines</h2>
          {s && metrics.next_booking?.[s] && <NextBooking scores={metrics.next_booking[s]} defs={metrics.definitions} />}
          {s && metrics.by_persona?.[s] && <PersonaHits byModel={metrics.by_persona[s]} />}
          {metrics.recovery && <Recovery recovery={metrics.recovery} />}

          <Optional title="Reliability before / after calibration" present={!!readCalibration(metrics)}
            waiting="Appears when metrics.json has a calibration section (rhythm calibrate stage).">
            <ReliabilityCard rel={readCalibration(metrics)!} />
          </Optional>
          <Optional title="Reminder precision / recall vs τ" present={!!readReminders(metrics)}
            waiting="Appears when metrics.json has a reminders section with a τ sweep.">
            <ReminderCard curve={readReminders(metrics)!} />
          </Optional>
          <Optional title="Ablation" present={!!firstTable(ablation)} waiting="Appears when metrics.json has an ablation section.">
            <GenericCard title="Ablation" hint="Each row removes or swaps one stage" value={ablation} />
          </Optional>
          <Optional title="Hidden personas" present={!!firstTable(hidden)} waiting="Appears once the final dataset with the two hidden personas is evaluated.">
            <GenericCard title="Hidden personas" hint="Personas the engine never saw during development, scored separately" value={hidden} prefer={['engine', 'by_persona']} />
          </Optional>
          {extra.map(([k, v]) => <GenericCard key={k} title={label(k)} hint="New section in metrics.json" value={v} />)}
        </>
      )}

      <h2 className="section">Detection accuracy (C1)</h2>
      <Detection metrics={fusion ?? embeddedFusion ?? null} loading={fusion === undefined} />
    </div>
  )
}

// ------------------------------------------------------------------ next booking
function NextBooking({ scores, defs }: { scores: Record<string, ModelScore>; defs?: Record<string, string> }) {
  const t = useTokens()
  const models = Object.keys(scores)
  const hit = useMemo(() => barsOption(t, scores, 'hit_at_1', pct), [t, scores])
  const mae = useMemo(() => barsOption(t, scores, 'time_mae_min', (v) => `${v.toFixed(1)} min`, true), [t, scores])
  const table = asTable(Object.fromEntries(models.map((m) => [modelName(m), scores[m]])))

  return (
    <div className="grid-even">
      <section className="card">
        <div className="card-head"><h3>Hit@1: next booking</h3><span className="hint">{defs?.hit_at_1 ? 'same day + lot, start within tolerance' : 'higher is better'}</span></div>
        <Chart option={hit} className="chart short" label="Hit@1 for the engine and each baseline" />
      </section>
      <section className="card">
        <div className="card-head"><h3>Start-time error</h3><span className="hint">mean |error| when day and lot matched · lower is better</span></div>
        <Chart option={mae} className="chart short" label="Mean start-time error in minutes for the engine and each baseline" />
      </section>
      {table && (
        <section className="card span2">
          <details>
            <summary>All next-booking numbers{defs ? ' and definitions' : ''}</summary>
            <TableView table={table} />
            {defs && <dl className="kv defs">{Object.entries(defs).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>}
          </details>
        </section>
      )}
    </div>
  )
}

function barsOption(t: Tokens, scores: Record<string, ModelScore>, key: 'hit_at_1' | 'time_mae_min', fmt: (v: number) => string, lowerBetter = false): Option {
  const b = base(t)
  const models = Object.keys(scores)
  return {
    textStyle: b.textStyle, grid: { left: 110, right: 56, top: 8, bottom: 8 },
    tooltip: { ...b.tooltip, trigger: 'item', formatter: (p: { name: string; value: number }) => `<b>${p.name}</b><br/>${fmt(p.value)}${lowerBetter ? ' (lower is better)' : ''}` },
    xAxis: { ...b.axis, type: 'value', show: false, max: key === 'hit_at_1' ? 1 : undefined },
    yAxis: { ...b.axis, type: 'category', inverse: true, data: models.map(modelName), axisLine: { show: false } },
    series: [{ type: 'bar', barMaxWidth: 20, data: models.map((m) => ({ value: scores[m][key] ?? 0, itemStyle: { color: modelColor(t, m), borderRadius: [0, 4, 4, 0] } })),
      label: { show: true, position: 'right', color: t.fg, fontSize: 12, formatter: (p: { value: number }) => fmt(p.value) } }],
  }
}

// ------------------------------------------------------------------ per persona
function PersonaHits({ byModel }: { byModel: Record<string, Record<string, { hit_at_1: number; n: number }>> }) {
  const t = useTokens()
  const models = useMemo(() => Object.keys(byModel), [byModel])
  const [vs, setVs] = useState(models.includes('B2') ? 'B2' : models.find((m) => m !== ENGINE) ?? ENGINE)
  const personas = useMemo(() => [...new Set(models.flatMap((m) => Object.keys(byModel[m])))].sort(), [models, byModel])
  const option = useMemo<Option>(() => {
    const b = base(t)
    const shown = [ENGINE, vs].filter((m, i, a) => byModel[m] && a.indexOf(m) === i)
    return {
      textStyle: b.textStyle, grid: { left: 130, right: 24, top: 8, bottom: 24 },
      tooltip: { ...b.tooltip, trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps: { seriesName: string; name: string; value: number; dataIndex: number }[]) =>
          `<b>${label(ps[0].name)}</b>` + ps.map((p) => `<br/>${p.seriesName}: ${pct(p.value)} (n=${byModel[shown[ps.indexOf(p)]]?.[personas[p.dataIndex]]?.n ?? '—'})`).join('') },
      xAxis: { ...b.axis, type: 'value', max: 1, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => pct(v) } },
      yAxis: { ...b.axis, type: 'category', inverse: true, data: personas, axisLabel: { ...b.axis.axisLabel, formatter: (v: string) => label(v) } },
      series: shown.map((m) => ({ name: modelName(m), type: 'bar', barMaxWidth: 10, barGap: '20%',
        data: personas.map((p) => byModel[m][p]?.hit_at_1 ?? 0), itemStyle: { color: modelColor(t, m), borderRadius: [0, 4, 4, 0] } })),
    }
  }, [t, byModel, vs, personas])
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>Hit@1 by persona</h3><span className="hint">Which kinds of driver the engine gets right</span>
        <span className="spacer" style={{ flex: 1 }} />
        <ModelLegend vs={vs} />
        <label className="field">vs <select value={vs} onChange={(e) => setVs(e.target.value)}>{models.filter((m) => m !== ENGINE).map((m) => <option key={m}>{m}</option>)}</select></label>
      </div>
      <Chart option={option} style={{ height: 40 + personas.length * 30 }} label="Hit@1 per persona, engine against the chosen baseline" />
    </section>
  )
}

function ModelLegend({ vs }: { vs: string }) {
  const t = useTokens()
  return <span className="legend"><span><i className="box" style={{ background: t.accent }} />{modelName(ENGINE)}</span><span><i className="box" style={{ background: t.faint }} />{vs}</span></span>
}

// ------------------------------------------------------------------ day-set recovery
type Rec = NonNullable<RhythmMetrics['recovery']>
function Recovery({ recovery }: { recovery: Rec }) {
  const t = useTokens()
  const models = useMemo(() => Object.keys(recovery), [recovery])
  const vs = models.find((m) => m !== ENGINE) ?? ENGINE
  const personas = useMemo(() => [...new Set(models.flatMap((m) => Object.entries(recovery[m].by_persona).filter(([, r]) => (r.routines ?? 0) > 0).map(([p]) => p)))].sort(), [models, recovery])
  const noRoutine = [...new Set(models.flatMap((m) => Object.entries(recovery[m].by_persona).filter(([, r]) => r.false_habit_rate != null).map(([p]) => p)))].sort()
  const option = useMemo<Option>(() => {
    const b = base(t)
    const shown = [ENGINE, vs].filter((m, i, a) => recovery[m] && a.indexOf(m) === i)
    return {
      textStyle: b.textStyle, grid: { left: 130, right: 24, top: 8, bottom: 24 },
      tooltip: { ...b.tooltip, trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps: { seriesName: string; name: string; dataIndex: number }[]) => `<b>${label(ps[0].name)}</b>` + ps.map((p, i) => {
          const r = recovery[shown[i]].by_persona[personas[p.dataIndex]] ?? {}
          return `<br/>${p.seriesName}: exact ${pct(r.exact_day_set ?? 0)} · Jaccard ${(r.day_jaccard ?? 0).toFixed(2)} · lot ${pct(r.lot_accuracy ?? 0)}${r.time_err_min != null ? ` · ±${r.time_err_min} min` : ''}`
        }).join('') },
      xAxis: { ...b.axis, type: 'value', max: 1, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => pct(v) } },
      yAxis: { ...b.axis, type: 'category', inverse: true, data: personas, axisLabel: { ...b.axis.axisLabel, formatter: (v: string) => label(v) } },
      series: shown.map((m) => ({ name: modelName(m), type: 'bar', barMaxWidth: 10, barGap: '20%',
        data: personas.map((p) => recovery[m].by_persona[p]?.exact_day_set ?? 0), itemStyle: { color: modelColor(t, m), borderRadius: [0, 4, 4, 0] } })),
    }
  }, [t, recovery, vs, personas])
  const overall = (m: string) => recovery[m]?.overall ?? {}

  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>Routine recovery vs truth.json</h3><span className="hint">Share of true routines whose exact day set was found</span>
        <span className="spacer" style={{ flex: 1 }} /><ModelLegend vs={vs} />
      </div>
      <div className="tiles compact">
        {(['exact_day_set', 'day_jaccard', 'lot_accuracy', 'time_err_min', 'false_habit_rate'] as const).map((k) => (
          <div key={k} className="tile">
            <span className="tile-label">{k === 'exact_day_set' ? 'Exact day set' : k === 'day_jaccard' ? 'Day Jaccard' : k === 'lot_accuracy' ? 'Lot accuracy' : k === 'time_err_min' ? 'Time error' : 'False habits'}</span>
            <span className="tile-value small">{fmtRec(k, overall(ENGINE)[k])}</span>
            <span className="tile-sub">{vs}: {fmtRec(k, overall(vs)[k])}</span>
          </div>
        ))}
      </div>
      <Chart option={option} style={{ height: 40 + personas.length * 30 }} label="Exact day-set recovery per persona, engine against the baseline" />
      {noRoutine.length > 0 && (
        <p className="muted small-print">
          Personas with no true routine (should get none): {noRoutine.map((p) => `${label(p)} ${fmtRec('false_habit_rate', recovery[ENGINE]?.by_persona[p]?.false_habit_rate)}`).join(' · ')} false-habit rate.
        </p>
      )}
    </section>
  )
}
const fmtRec = (k: string, v: unknown) => (typeof v !== 'number' ? '—' : k === 'time_err_min' ? `${v.toFixed(1)} min` : k === 'day_jaccard' ? v.toFixed(2) : pct(v))

// ------------------------------------------------------------------ calibration
function ReliabilityCard({ rel }: { rel: Reliability }) {
  const t = useTokens()
  const option = useMemo<Option>(() => {
    const b = base(t)
    const series = (name: string, bins: Reliability['before'], color: string) => ({
      name, type: 'line', data: bins.map((x) => [x.predicted, x.observed, x.n ?? null]), symbol: 'circle', symbolSize: 8,
      lineStyle: { color, width: 2 }, itemStyle: { color, borderColor: t.surface, borderWidth: 2 },
    })
    return {
      textStyle: b.textStyle, grid: { left: 48, right: 24, top: 16, bottom: 40 },
      tooltip: { ...b.tooltip, trigger: 'item', formatter: (p: { seriesName: string; data: [number, number, number | null] }) =>
        `<b>${p.seriesName}</b><br/>predicted ${pct(p.data[0])} → happened ${pct(p.data[1])}${p.data[2] != null ? ` (n=${p.data[2]})` : ''}` },
      xAxis: { ...b.axis, type: 'value', min: 0, max: 1, name: 'predicted probability', nameLocation: 'middle', nameGap: 26, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => pct(v) } },
      yAxis: { ...b.axis, type: 'value', min: 0, max: 1, name: 'observed', axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => pct(v) } },
      series: [
        { name: 'perfect', type: 'line', data: [[0, 0], [1, 1]], symbol: 'none', silent: true, lineStyle: { color: t['line-strong'], type: 'dashed', width: 1 } },
        ...(rel.before.length ? [series('Before (raw)', rel.before, t['series-forecast'])] : []),
        ...(rel.after.length ? [series('After calibration', rel.after, t.accent)] : []),
      ],
    }
  }, [t, rel])
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>Reliability before / after calibration</h3><span className="hint">Closer to the diagonal = probabilities mean what they say</span>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="legend">
          {rel.before.length > 0 && <span><i style={{ background: t['series-forecast'] }} />Before</span>}
          {rel.after.length > 0 && <span><i style={{ background: t.accent }} />After</span>}
        </span>
      </div>
      <div className="grid2 tight">
        <Chart option={option} className="chart" label="Reliability diagram: predicted against observed frequency, before and after calibration" />
        {rel.stats.length > 0 && (
          <table className="compact-table"><thead><tr><th /><th className="num">Before</th><th className="num">After</th></tr></thead>
            <tbody>{rel.stats.map((x) => <tr key={x.label}><td>{x.label}</td><td className="num">{x.before?.toFixed(3) ?? '—'}</td><td className="num">{x.after?.toFixed(3) ?? '—'}</td></tr>)}</tbody>
          </table>
        )}
      </div>
    </section>
  )
}

// ------------------------------------------------------------------ reminders
function ReminderCard({ curve }: { curve: ReminderCurve }) {
  const t = useTokens()
  const { points } = curve
  const initial = curve.chosen != null ? points.reduce((bi, p, i) => (Math.abs(p.tau - curve.chosen!) < Math.abs(points[bi].tau - curve.chosen!) ? i : bi), 0) : Math.floor(points.length / 2)
  const [idx, setIdx] = useState(initial)
  const cur = points[Math.min(idx, points.length - 1)]
  const option = useMemo<Option>(() => {
    const b = base(t)
    const line = (name: string, key: 'precision' | 'recall', color: string) => ({
      name, type: 'line', data: points.map((p) => [p.tau, p[key]]), showSymbol: false, symbolSize: 8,
      lineStyle: { color, width: 2 }, itemStyle: { color, borderColor: t.surface, borderWidth: 2 },
    })
    return {
      animation: false, // the slider redraws on every step
      textStyle: b.textStyle, grid: { left: 48, right: 24, top: 16, bottom: 40 },
      tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: unknown) => (typeof v === 'number' ? pct(v) : String(v)),
        axisPointer: { type: 'line', lineStyle: { color: t['line-strong'] } } },
      xAxis: { ...b.axis, type: 'value', min: points[0].tau, max: points.at(-1)!.tau, name: 'τ (reminder threshold)', nameLocation: 'middle', nameGap: 26 },
      yAxis: { ...b.axis, type: 'value', min: 0, max: 1, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => pct(v) } },
      series: [
        { ...line('Precision', 'precision', t['series-forecast']),
          markLine: { silent: true, symbol: 'none', data: [
            { xAxis: cur.tau, lineStyle: { color: t.fg, width: 1 }, label: { formatter: `τ ${cur.tau.toFixed(2)}`, color: t.fg, fontSize: 11 } },
            ...(curve.target != null ? [{ yAxis: curve.target, lineStyle: { color: t.muted, type: 'dashed' as const, width: 1 }, label: { formatter: `precision target ${pct(curve.target)}`, position: 'insideStartTop' as const, color: t.muted, fontSize: 11 } }] : []),
          ] } },
        line('Recall', 'recall', t.accent),
      ],
    }
  }, [t, points, cur, curve.target])
  const extra = Object.entries(cur).filter(([k]) => !['tau', 'precision', 'recall'].includes(k))
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>Reminder precision / recall vs τ</h3><span className="hint">Raise τ: fewer, surer reminders</span>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="legend"><span><i style={{ background: t['series-forecast'] }} />Precision</span><span><i style={{ background: t.accent }} />Recall</span></span>
      </div>
      <Chart option={option} label="Reminder precision and recall as the threshold tau changes" />
      <div className="tau-row">
        <label className="field" htmlFor="tau">τ</label>
        <input id="tau" type="range" min={0} max={points.length - 1} value={idx} onChange={(e) => setIdx(+e.target.value)} aria-valuetext={`tau ${cur.tau.toFixed(2)}`} />
        <span className="num"><b>{cur.tau.toFixed(2)}</b></span>
        <span>precision <b className="num">{pct(cur.precision)}</b></span>
        <span>recall <b className="num">{pct(cur.recall)}</b></span>
        {extra.map(([k, v]) => <span key={k} className="muted">{label(k)} <b className="num">{fmtMetric(k, v)}</b></span>)}
        {curve.chosen != null && <span className="pill auto">engine uses τ = {curve.chosen.toFixed(2)}</span>}
      </div>
    </section>
  )
}

// ------------------------------------------------------------------ detection (C1)
const DETECTION_TILES: [key: string, label: string, kind: 'rate' | 'min' | 'count', good: 'high' | 'low'][] = [
  ['auto_confirm_rate', 'Auto-confirmed arrivals', 'rate', 'high'],
  ['false_confirm_rate', 'False confirms (no-shows)', 'rate', 'low'],
  ['median_time_to_confirm_min', 'Time to confirm (median)', 'min', 'low'],
  ['p90_time_to_confirm_min', 'Time to confirm (p90)', 'min', 'low'],
  ['ask_driver_rate', 'Asked the driver', 'rate', 'low'],
  ['missed_arrival_rate', 'Missed arrivals', 'rate', 'low'],
  ['departure.auto_checkout_rate', 'Auto check-outs', 'rate', 'high'],
  ['departure.median_out_at_error_min', 'Exit time error (median)', 'min', 'low'],
]
const SPLITS = ['test', 'all', 'tune']

function Detection({ metrics, loading }: { metrics: FusionMetrics | null; loading: boolean }) {
  const report = metrics && isObj(metrics) ? metrics : null
  const splits = report ? SPLITS.filter((k) => isObj(report[k])) : []
  const [split, setSplit] = useState('test')
  if (loading) return <p className="muted">Looking for fusion metrics…</p>
  if (!report) {
    return (
      <section className="card empty-card">
        <h3>Waiting for simulator results</h3>
        <p className="muted">
          Auto-confirm rate, false-confirm rate and time to confirm appear here once the ops simulator has replayed
          <code> data/traces/</code> (<code>tools/simulator evaluate</code> → <code>runs/fusion_metrics.json</code>) and the API serves that file.
        </p>
      </section>
    )
  }
  const sp = splits.includes(split) ? split : splits[0]
  const score = (sp ? report[sp] : report) as FusionScore
  const flat = asTable({ x: score })?.rows[0]?.cells ?? {}
  const tiles = DETECTION_TILES.filter(([k]) => typeof flat[k] === 'number')
  const groups = Object.entries(report).filter(([k, v]) => !SPLITS.includes(k) && asTable(v))
  return (
    <>
      <div className="toolbar" style={{ marginBottom: 8 }}>
        <span className="muted" style={{ fontSize: 'var(--fs-sm)' }}>
          Fusion replayed over the driver/camera traces and scored against their ground truth
          {typeof score.bookings === 'number' ? ` · ${score.bookings} bookings, ${score.arrivals ?? '?'} arrivals, ${score.no_shows ?? '?'} no-shows` : ''}
        </span>
        <span className="spacer" />
        {splits.length > 1 && (
          <div className="segmented" role="group" aria-label="Trace split">
            {splits.map((x) => <button key={x} className={x === sp ? 'on' : ''} aria-pressed={x === sp} onClick={() => setSplit(x)}>{x === 'all' ? 'all traces' : `${x} traces`}</button>)}
          </div>
        )}
      </div>
      <div className="tiles">
        {tiles.map(([k, name, kind, good]) => {
          const v = flat[k] as number
          return (
            <div key={k} className={`tile ${k === 'auto_confirm_rate' || k === 'false_confirm_rate' ? 'accent' : ''}`}>
              <span className="tile-label">{name}</span>
              <span className="tile-value small">{kind === 'rate' ? `${(v * 100).toFixed(1)}%` : kind === 'min' ? `${v.toFixed(1)} min` : v}</span>
              <span className="tile-sub">{good === 'high' ? 'higher is better' : 'lower is better'}</span>
            </div>
          )
        })}
      </div>
      {groups.map(([k, v]) => <GenericCard key={k} title={label(k)} hint="Same metrics per group; departure.* columns score check-outs" value={v} />)}
      {!tiles.length && !groups.length && <GenericCard title="Fusion metrics" value={{ [sp ?? 'all']: score }} />}
    </>
  )
}

// ------------------------------------------------------------------ generic
function Optional({ title, present, waiting, children }: { title: string; present: boolean; waiting: string; children: ReactNode }) {
  if (present) return <>{children}</>
  return <p className="pending"><b>{title}:</b> {waiting}</p>
}

function GenericCard({ title, hint, value, prefer }: { title: string; hint?: string; value: unknown; prefer?: string[] }) {
  const found = firstTable(value, prefer)
  if (!found) return null
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div className="card-head"><h3>{title}</h3>{hint && <span className="hint">{hint}</span>}{found.path && <span className="pill">{found.path.split('.').map(label).join(' › ')}</span>}</div>
      <TableView table={found.table} />
    </section>
  )
}

function TableView({ table }: { table: Table }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th />{table.columns.map((c) => <th key={c} className="num">{label(c)}</th>)}</tr></thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={r.key}><td>{label(r.key)}</td>{table.columns.map((c) => <td key={c} className="num">{fmtMetric(c, r.cells[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
