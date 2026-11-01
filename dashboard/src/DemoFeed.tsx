// Demo banner: a running narration of what the system decided, across all lots, built from the audit log
// (every stay decision, scan and alert resolution) plus the open alerts (overstay fees, unknown cars).
// It works for any replay (tools/demo.py, the simulator, a phone in the room), not only the scripted one.
import { useEffect, useRef, useState } from 'react'
import { api, hhmm, pctFine } from './api'
import type { Alert, AuditEntry, Stay } from './contract'

const POLL_MS = 2500
const SHOWN = 8
const ALERT_TYPES = new Set(['low_confidence', 'anomaly', 'overstay', 'unknown_vehicle'])

type Tone = 'good' | 'warning' | 'critical' | 'info'
interface FeedItem { key: string; at: string; lot: string; text: string; tone: Tone; link?: string }

type AuditRow = AuditEntry & { lot_id?: string | null }
const stayOf = (a: AuditRow) => ((a.after ?? a.before) as Stay | null) ?? null

/** One sentence per audit action; null for actions the audience doesn't need. */
function fromAudit(a: AuditRow): FeedItem | null {
  const st = stayOf(a)
  const plate = st?.plate_norm ?? null
  const car = plate ?? 'an unidentified car'
  const by = a.device_id?.startsWith('attendant') || a.actor_type === 'attendant' ? 'the attendant' : a.actor_type === 'driver' ? 'the driver' : 'the system'
  const p = st?.in_conf != null ? ` · P ${pctFine(st.in_conf)}` : ''
  const texts: Record<string, [string, Tone] | undefined> = {
    auto_check_in: [`${car} checked in automatically${p}`, 'good'],
    arriving: [`${car} arriving${p}: in the ask-driver band`, 'warning'],
    check_in: [`${car} checked in by ${by}`, 'good'],
    walk_in: [`walk-in ${car} registered by ${by}`, 'info'],
    auto_check_out: [`${car} left: automatic check-out`, 'info'],
    check_out: [`${car} checked out by ${by}`, 'info'],
    inferred_check_out: [`${car} checked out by inference (silent departure)`, 'info'],
    corrected_check_out: [`${car}: departure time corrected by a later exit`, 'info'],
    closed_by_arrival_elsewhere: [`${car} arrived at another lot: stay here closed`, 'info'],
    gate_unidentified_entry: ['gate sensor: a car entered with no plate read, counted as unidentified', 'warning'],
    gate_unidentified_exit: ['gate sensor: an unidentified car left', 'info'],
    gate_identified: ['the unidentified car is identified: its booking checked in, placeholder closed', 'good'],
    resolve_alert: [`alert ${a.entity_id} resolved by ${by}`, 'good'],
    override: [`attendant override on ${a.entity_id}`, 'info'],
  }
  const hit = texts[a.action]
  if (!hit) return null
  const lot = a.lot_id ?? st?.lot_id ?? ''
  const link = a.entity === 'stay' && st ? `#detection/${st.lot_id}/${encodeURIComponent(st.id)}` : undefined
  return { key: `a${a.id}`, at: a.observed_at, lot, text: hit[0], tone: hit[1], link }
}

function fromAlert(a: Alert): FeedItem {
  return { key: `al${a.id}`, at: a.created_at, lot: a.lot_id, text: a.message,
    tone: a.severity === 'critical' ? 'critical' : 'warning' }
}

export function DemoFeed({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<FeedItem[]>([])
  const [error, setError] = useState(false)
  const seen = useRef(new Map<string, FeedItem>())
  const maxId = useRef(0)

  useEffect(() => {
    let alive = true
    const load = () => Promise.all([api.auditAll(60), api.alertsAll()]).then(([audit, alerts]) => {
      if (!alive) return
      const top = Math.max(0, ...audit.map((a) => a.id))
      if (top < maxId.current) seen.current.clear()      // /dev/reset: a new demo started
      maxId.current = top
      for (const a of audit) { const f = fromAudit(a); if (f) seen.current.set(f.key, f) }
      for (const a of alerts) if (ALERT_TYPES.has(a.type)) { const f = fromAlert(a); seen.current.set(f.key, f) }
      const all = [...seen.current.values()].sort((x, y) => Date.parse(y.at) - Date.parse(x.at) || y.key.localeCompare(x.key))
      seen.current = new Map(all.slice(0, 60).map((f) => [f.key, f]))
      setItems(all.slice(0, SHOWN)); setError(false)
    }).catch(() => alive && setError(true))
    load()
    const id = setInterval(load, POLL_MS)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const [latest, ...rest] = items
  return (
    <section className="demo-feed" aria-label="Demo narration" aria-live="polite">
      <div className="demo-head">
        <span className="pill demo-pill">Demo</span>
        {latest ? (
          <span className={`demo-latest ${latest.tone}`}>
            <span className="num muted">{hhmm(latest.at)}</span> <b>{latest.lot}</b> {latest.link ? <a href={latest.link}>{latest.text}</a> : latest.text}
          </span>
        ) : <span className="muted">{error ? 'Waiting for the API…' : 'Waiting for the first event. Start it with uv run python -m tools.demo'}</span>}
        <button className="linkish demo-close" onClick={onClose} aria-label="Hide the demo feed">Hide</button>
      </div>
      {rest.length > 0 && (
        <ol className="demo-list">
          {rest.map((f) => (
            <li key={f.key} className={f.tone}>
              <span className="num muted">{hhmm(f.at)}</span> <b>{f.lot}</b> {f.link ? <a href={f.link}>{f.text}</a> : f.text}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
