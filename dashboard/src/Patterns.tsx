import { useEffect, useMemo, useState } from 'react'
import { api, dayTime, label, openUser, pct } from './api'
import type { PatternRow, PatternState, ReminderTier, RunInfo } from './contract'

const STATES: PatternState[] = ['active', 'emerging', 'fading', 'ended']
const TIERS: ReminderTier[] = ['locked_in', 'at_risk', 'emerging', 'none']
type SortKey = 'user' | 'confidence' | 'next' | 'support'
const SORTS: Record<SortKey, (a: PatternRow, b: PatternRow) => number> = {
  user: (a, b) => a.user_id.localeCompare(b.user_id) || Number(b.is_primary) - Number(a.is_primary),
  confidence: (a, b) => a.confidence - b.confidence,
  next: (a, b) => (a.next_booking?.at ?? '9').localeCompare(b.next_booking?.at ?? '9'),
  support: (a, b) => a.support - b.support,
}

export function Patterns() {
  const [rows, setRows] = useState<PatternRow[]>([])
  const [run, setRun] = useState<RunInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [state, setState] = useState<string>('')
  const [tier, setTier] = useState<string>('')
  const [lot, setLot] = useState<string>('')
  const [minConf, setMinConf] = useState(0)
  const [allRoutines, setAllRoutines] = useState(false)
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'confidence', desc: true })

  useEffect(() => {
    Promise.all([api.run(), api.patterns(false)])
      .then(([r, p]) => { setRun(r); setRows(p) })
      .catch((e) => setError(`No pattern run found. Run \`uv run python -m rhythm run\`. (${e})`))
  }, [])

  const lots = useMemo(() => [...new Set(rows.map((r) => r.location_id).filter(Boolean) as string[])].sort(), [rows])
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const list = rows.filter((r) =>
      (allRoutines || r.is_primary)
      && (!state || (state === 'none' ? r.state == null : r.state === state))
      && (!tier || r.reminder.tier === tier)
      && (!lot || r.location_id === lot)
      && r.confidence * 100 >= minConf
      && (!needle || `${r.user_id} ${r.label} ${r.location_id} ${r.reason}`.toLowerCase().includes(needle)))
    const cmp = SORTS[sort.key]
    return list.sort((a, b) => (sort.desc ? -cmp(a, b) : cmp(a, b)))
  }, [rows, q, state, tier, lot, minConf, allRoutines, sort])

  const sortBy = (key: SortKey) => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== 'user' && key !== 'next' }))
  const arrow = (key: SortKey) => (sort.key === key ? (sort.desc ? ' ↓' : ' ↑') : '')
  const reset = () => { setQ(''); setState(''); setTier(''); setLot(''); setMinConf(0) }
  const users = new Set(shown.map((r) => r.user_id)).size

  return (
    <div className="page">
      <div className="toolbar">
        <input aria-label="Search" placeholder="Search user, days, lot, reason" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="field">State
          <select value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">All</option>
            {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
            <option value="none">No routine</option>
          </select>
        </label>
        <label className="field">Reminder
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="">All</option>
            {TIERS.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </select>
        </label>
        <label className="field">Lot
          <select value={lot} onChange={(e) => setLot(e.target.value)}>
            <option value="">All</option>
            {lots.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
        <label className="field">Confidence ≥ <b className="num" style={{ minWidth: 34 }}>{minConf}%</b>
          <input type="range" min={0} max={100} step={5} value={minConf} onChange={(e) => setMinConf(+e.target.value)} />
        </label>
        <label className="field"><input type="checkbox" checked={allRoutines} onChange={(e) => setAllRoutines(e.target.checked)} /> All routines</label>
        <button className="linkish" onClick={reset}>Reset</button>
        <span className="spacer" />
        <button className="button" onClick={() => downloadCsv(shown)}>Export view (CSV)</button>
        <a className="button" href={api.exportUrl('csv')}>Full CSV</a>
        <a className="button" href={api.exportUrl('json')} target="_blank" rel="noreferrer">JSON</a>
      </div>
      <p className="muted" style={{ fontSize: 'var(--fs-sm)', margin: '0 0 10px' }}>
        {shown.length} {allRoutines ? 'routines' : 'users'}{allRoutines && ` across ${users} users`} of {rows.filter((r) => allRoutines || r.is_primary).length}
        {run && <> · run {run.run_id} · data up to {run.cutoff} · {run.model_version}</>}
      </p>
      {error && <p className="error">{error}</p>}
      <div className="card table-wrap" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th className="sortable" onClick={() => sortBy('user')}>User{arrow('user')}</th>
              <th>Pattern</th><th>Time · place</th>
              <th className="sortable" onClick={() => sortBy('next')}>Next likely booking{arrow('next')}</th>
              <th className="sortable" onClick={() => sortBy('confidence')}>Confidence{arrow('confidence')}</th>
              <th className="sortable num" onClick={() => sortBy('support')}>Bookings{arrow('support')}</th>
              <th>Reminder</th><th>Why</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.pattern_id} className="clickable" onClick={() => openUser(r.user_id)}>
                <td><a href={`#user/${r.user_id}`} onClick={(e) => e.stopPropagation()}>{r.user_id}</a>{!r.is_primary && <span className="muted"> · extra</span>}</td>
                <td>{r.label}{r.state && <> <span className={`pill ${r.state}`}>{r.state}</span></>}</td>
                <td>{r.time_window ? `${r.time_window.center} ±${r.time_window.spread_min}′` : 'Variable'} · {r.location_id ?? '—'}</td>
                <td>{r.next_booking ? dayTime(r.next_booking.at) : <span className="muted">Not predictable</span>}</td>
                <td className="num"><div className="bar"><span style={{ width: pct(r.confidence) }} /></div>{pct(r.confidence)}</td>
                <td className="num">{r.support}</td>
                <td><span className={`pill ${r.reminder.tier}`}>{label(r.reminder.tier)}</span></td>
                <td className="reason">{r.reason}</td>
              </tr>
            ))}
            {shown.length === 0 && !error && <tr><td colSpan={8} className="muted">No patterns match these filters.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function downloadCsv(rows: PatternRow[]) {
  const cols: [string, (r: PatternRow) => unknown][] = [
    ['user_id', (r) => r.user_id], ['pattern_id', (r) => r.pattern_id], ['is_primary', (r) => r.is_primary],
    ['pattern_type', (r) => r.pattern_type], ['state', (r) => r.state], ['label', (r) => r.label], ['days', (r) => r.days.join(' ')],
    ['time_center', (r) => r.time_window?.center], ['spread_min', (r) => r.time_window?.spread_min], ['location_id', (r) => r.location_id],
    ['confidence', (r) => r.confidence], ['next_booking_at', (r) => r.next_booking?.at], ['reminder_tier', (r) => r.reminder.tier],
    ['support', (r) => r.support], ['change_date', (r) => r.change_date], ['reason', (r) => r.reason],
  ]
  const esc = (v: unknown) => (v == null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replaceAll('"', '""')}"` : String(v))
  const text = [cols.map((c) => c[0]).join(','), ...rows.map((r) => cols.map(([, f]) => esc(f(r))).join(','))].join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  a.download = 'patterns_view.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}
