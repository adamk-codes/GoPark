import { useEffect, useMemo, useState } from 'react'
import { api, pct } from './api'
import type { PatternRow, RunInfo } from './contract'

export function Patterns() {
  const [rows, setRows] = useState<PatternRow[]>([])
  const [run, setRun] = useState<RunInfo | null>(null)
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.run(), api.patterns()])
      .then(([r, p]) => { setRun(r); setRows(p) })
      .catch((e) => setError(`No pattern run found. Run \`uv run python -m rhythm run\`. (${e})`))
  }, [])

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const list = q ? rows.filter((r) => `${r.user_id} ${r.label} ${r.location_id}`.toLowerCase().includes(q)) : rows
    return [...list].sort((a, b) => b.confidence - a.confidence)
  }, [rows, filter])

  return (
    <div className="page">
      <div className="toolbar">
        <label htmlFor="q">Search</label>
        <input id="q" placeholder="User, days or lot" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <a className="button" href={api.exportUrl('csv')}>Export CSV</a>
        <a className="button" href={api.exportUrl('json')} target="_blank" rel="noreferrer">Open JSON</a>
        {run && <span className="muted">Run {run.run_id} · data up to {run.cutoff} · {run.model_version}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr><th>User</th><th>Pattern</th><th>Time · place</th><th>Next likely booking</th><th>Confidence</th><th>Reminder</th><th>Why</th></tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.pattern_id}>
              <td>{r.user_id}</td>
              <td>{r.label}{r.state && <span className={`pill ${r.state}`}>{r.state}</span>}</td>
              <td>{r.time_window ? r.time_window.center : 'Variable'} · {r.location_id ?? '—'}</td>
              <td>{r.next_booking ? new Date(r.next_booking.at).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Beirut' }) : 'Not predictable'}</td>
              <td><div className="bar"><span style={{ width: pct(r.confidence) }} /></div>{pct(r.confidence)}</td>
              <td>{r.reminder.recommend ? <span className={`pill ${r.reminder.tier}`}>{r.reminder.tier.replace('_', ' ')}</span> : <span className="muted">none</span>}</td>
              <td className="reason">{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
