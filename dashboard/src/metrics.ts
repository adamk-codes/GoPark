// Types + defensive readers for rhythm's metrics.json (GET /rhythm/runs/latest/evaluation) and the ops
// session's fusion metrics. metrics.json is free-form (CONTRACT.md §8), so the known core is typed and
// everything else is read by shape: a new section lights up on the Evaluation page without code changes
// as long as it is a table (object of objects, or array of objects with numeric fields).

export interface ModelScore {
  hit_at_1: number
  time_mae_min: number | null
  coverage?: number
  n?: number
  slot_precision?: number
  slot_recall?: number
  slot_f1?: number
  [k: string]: unknown
}

export interface RecoveryScore {
  routines?: number
  users_without_routine?: number
  day_jaccard?: number
  exact_day_set?: number
  lot_accuracy?: number
  time_err_min?: number | null
  false_habit_rate?: number
  [k: string]: unknown
}

export interface RhythmMetrics {
  run_id?: string
  end?: string
  definitions?: Record<string, string>
  next_booking?: Record<string, Record<string, ModelScore>> // split -> model -> score
  by_persona?: Record<string, Record<string, Record<string, { hit_at_1: number; n: number }>>> // split -> model -> persona
  by_cutoff?: Record<string, Record<string, ({ cutoff: string } & ModelScore)[]>>
  recovery?: Record<string, { overall: RecoveryScore; by_persona: Record<string, RecoveryScore> }> // model -> …
  [k: string]: unknown
}

/** One block of fusion scoring from the ops simulator (`tools/simulator evaluate`). */
export interface FusionScore {
  bookings?: number
  arrivals?: number
  no_shows?: number
  auto_confirm_rate?: number | null
  ask_driver_rate?: number | null
  missed_arrival_rate?: number | null
  false_confirm_rate?: number | null
  false_ask_rate?: number | null
  early_confirm_rate?: number | null
  median_time_to_confirm_min?: number | null
  p90_time_to_confirm_min?: number | null
  low_confidence_alerts?: number
  anomaly_alerts?: number
  departure?: Record<string, number | null>
  [k: string]: unknown
}

/** runs/fusion_metrics.json: {all, tune, test, by_scenario, by_lot_kind}. A flat FusionScore is accepted too. */
export interface FusionMetrics {
  all?: FusionScore
  tune?: FusionScore
  test?: FusionScore
  by_scenario?: Record<string, FusionScore>
  by_lot_kind?: Record<string, FusionScore>
  [k: string]: unknown
}

type Obj = Record<string, unknown>
export const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const pick = (o: Obj, keys: string[]): unknown => keys.map((k) => o[k]).find((v) => v !== undefined)

/** Find the first value under any of `keys`, searching nested objects breadth-first (max depth 3). */
export function findKey(root: unknown, keys: string[], depth = 3): unknown {
  let level: unknown[] = [root]
  for (let d = 0; d <= depth && level.length; d++) {
    const next: unknown[] = []
    for (const o of level) {
      if (!isObj(o)) continue
      const hit = pick(o, keys)
      if (hit !== undefined) return hit
      next.push(...Object.values(o))
    }
    level = next
  }
  return undefined
}

// ------------------------------------------------------------------ calibration
export interface ReliabilityBin { predicted: number; observed: number; n?: number }
export interface Reliability { before: ReliabilityBin[]; after: ReliabilityBin[]; stats: { label: string; before?: number; after?: number }[] }

const PRED_KEYS = ['predicted', 'mean_predicted', 'p_mean', 'pred', 'confidence', 'p', 'x']
const OBS_KEYS = ['observed', 'frac_positive', 'fraction_positive', 'empirical', 'actual', 'rate', 'y']

function toBins(v: unknown): ReliabilityBin[] | null {
  if (isObj(v)) {
    const inner = pick(v, ['bins', 'reliability', 'curve', 'points'])
    if (inner !== undefined) return toBins(inner)
    const ps = pick(v, PRED_KEYS), os = pick(v, OBS_KEYS)
    if (Array.isArray(ps) && Array.isArray(os)) {
      const ns = pick(v, ['n', 'count', 'counts'])
      return ps.map((p, i) => ({ predicted: +p, observed: +os[i], n: Array.isArray(ns) ? +ns[i] : undefined }))
    }
    return null
  }
  if (!Array.isArray(v) || !v.length) return null
  if (Array.isArray(v[0])) return v.map((b) => ({ predicted: +b[0], observed: +b[1], n: b[2] != null ? +b[2] : undefined }))
  const bins = v.filter(isObj).map((b) => ({ predicted: Number(pick(b, PRED_KEYS)), observed: Number(pick(b, OBS_KEYS)), n: isNum(pick(b, ['n', 'count'])) ? Number(pick(b, ['n', 'count'])) : undefined }))
  return bins.filter((b) => isNum(b.predicted) && isNum(b.observed))
}

export function readCalibration(m: RhythmMetrics): Reliability | null {
  let c = findKey(m, ['calibration', 'calibrate'], 1)
  if (isObj(c) && isObj(c.test) && !pick(c, ['before', 'raw', 'uncalibrated'])) c = c.test
  if (!isObj(c)) return null
  const beforeRaw = pick(c, ['before', 'raw', 'uncalibrated', 'pre'])
  const afterRaw = pick(c, ['after', 'calibrated', 'isotonic', 'post'])
  const before = toBins(beforeRaw), after = toBins(afterRaw)
  if (!before?.length && !after?.length) return null
  const stats = ['brier', 'ece', 'log_loss'].map((k) => ({
    label: k === 'ece' ? 'ECE' : k === 'brier' ? 'Brier' : 'Log loss',
    before: isObj(beforeRaw) && isNum(beforeRaw[k]) ? beforeRaw[k] : isNum(c[`${k}_before`]) ? (c[`${k}_before`] as number) : undefined,
    after: isObj(afterRaw) && isNum(afterRaw[k]) ? afterRaw[k] : isNum(c[`${k}_after`]) ? (c[`${k}_after`] as number) : undefined,
  })).filter((s) => s.before != null || s.after != null)
  return { before: before ?? [], after: after ?? [], stats }
}

// ------------------------------------------------------------------ reminders
export interface TauPoint { tau: number; precision: number; recall: number; [k: string]: number }
export interface ReminderCurve { points: TauPoint[]; chosen: number | null; target: number | null }

export function readReminders(m: RhythmMetrics): ReminderCurve | null {
  const r = findKey(m, ['reminders', 'reminder', 'remind'], 1)
  if (r === undefined) return null
  const arr = Array.isArray(r) ? r : isObj(r) ? pick(r, ['curve', 'pr_curve', 'by_tau', 'sweep', 'points', 'thresholds']) ?? findArrayWith(r, ['precision', 'recall']) : null
  let points: TauPoint[] = []
  if (Array.isArray(arr)) {
    points = arr.filter(isObj).map((p) => {
      const tau = Number(pick(p, ['tau', 'threshold', 't']))
      const nums = Object.fromEntries(Object.entries(p).filter(([, v]) => isNum(v))) as Record<string, number>
      return { ...nums, tau, precision: Number(p.precision), recall: Number(p.recall) }
    }).filter((p) => isNum(p.tau) && isNum(p.precision) && isNum(p.recall)).sort((a, b) => a.tau - b.tau)
  } else if (isObj(arr) && Array.isArray(arr.tau)) {
    const t = arr.tau as number[], pr = arr.precision as number[], rc = arr.recall as number[]
    points = t.map((tau, i) => ({ tau, precision: pr?.[i], recall: rc?.[i] })).filter((p) => isNum(p.precision) && isNum(p.recall))
  }
  if (!points.length) return null
  const o = isObj(r) ? r : {}
  const chosen = pick(o, ['tau', 'chosen_tau', 'selected_tau', 'threshold'])
  const target = pick(o, ['precision_target', 'target_precision', 'target'])
  return { points, chosen: isNum(chosen) ? chosen : null, target: isNum(target) ? target : null }
}

function findArrayWith(o: Obj, fields: string[]): unknown[] | undefined {
  for (const v of Object.values(o)) {
    if (Array.isArray(v) && v.some((x) => isObj(x) && fields.every((f) => f in x))) return v
    if (isObj(v)) { const hit = findArrayWith(v, fields); if (hit) return hit }
  }
  return undefined
}

// ------------------------------------------------------------------ generic tables
export interface Table { columns: string[]; rows: { key: string; cells: Record<string, unknown> }[] }

/** Any object-of-objects or array-of-objects as a table of its scalar fields; null if it isn't tabular. */
export function asTable(v: unknown, keyField?: string): Table | null {
  let entries: [string, Obj][] = []
  if (Array.isArray(v)) {
    const objs = v.filter(isObj)
    if (!objs.length || objs.length < v.length) return null
    const kf = keyField ?? ['name', 'variant', 'stage', 'ablation', 'persona', 'model', 'scenario', 'id'].find((k) => objs.every((o) => typeof o[k] === 'string'))
    entries = objs.map((o, i) => [kf ? String(o[kf]) : String(i + 1), kf ? Object.fromEntries(Object.entries(o).filter(([k]) => k !== kf)) : o])
  } else if (isObj(v)) {
    const vals = Object.entries(v)
    if (!vals.length || !vals.every(([, x]) => isObj(x))) return null
    entries = vals as [string, Obj][]
  } else return null
  // One level of nesting is flattened ("departure.auto_checkout_rate") so grouped metrics still show.
  entries = entries.map(([key, o]) => [key, Object.fromEntries(Object.entries(o).flatMap(([k, x]) =>
    isObj(x) ? Object.entries(x).map(([k2, y]) => [`${k}.${k2}`, y]) : [[k, x]]))])
  const columns: string[] = []
  for (const [, o] of entries) for (const [k, x] of Object.entries(o)) {
    if ((isNum(x) || typeof x === 'string' || typeof x === 'boolean' || x === null) && !columns.includes(k)) columns.push(k)
  }
  if (!columns.length) return null
  return { columns, rows: entries.map(([key, cells]) => ({ key, cells })) }
}

const isScalar = (x: unknown) => x === null || ['number', 'string', 'boolean'].includes(typeof x)
/** Rows that are all flat records (no nested objects): the natural shape of a metrics table. */
const isPureTable = (v: unknown) => {
  const rows = Array.isArray(v) ? v : isObj(v) ? Object.values(v) : []
  return rows.length > 0 && rows.every((r) => isObj(r) && Object.values(r).every(isScalar))
}

/** The best table inside a metrics section: a flat table found breadth-first (preferring `prefer` keys),
 *  else the section itself with one level of nesting flattened. `path` says where it was found. */
export function firstTable(v: unknown, prefer: string[] = []): { path: string; table: Table } | null {
  let level: [string, unknown][] = [['', v]]
  for (let d = 0; d < 4 && level.length; d++) {
    const hit = level.find(([, x]) => isPureTable(x))
    if (hit) return { path: hit[0], table: asTable(hit[1])! }
    level = level.flatMap(([p, x]) => isObj(x)
      ? Object.keys(x).sort((a, b) => Number(prefer.includes(b)) - Number(prefer.includes(a))).map((k) => [p ? `${p}.${k}` : k, x[k]] as [string, unknown])
      : [])
  }
  const flat = asTable(v)
  return flat ? { path: '', table: flat } : null
}

// Keys the Evaluation page draws with dedicated charts; anything else at top level is shown generically.
export const KNOWN_KEYS = new Set(['run_id', 'input', 'end', 'definitions', 'cutoffs', 'next_booking', 'by_persona', 'by_cutoff',
  'recovery', 'calibration', 'calibrate', 'reminders', 'reminder', 'remind', 'ablation', 'ablations', 'hidden', 'hidden_personas',
  'fusion', 'detection'])

export const HIDDEN_KEYS = ['hidden_personas', 'hidden']
export const ABLATION_KEYS = ['ablation', 'ablations']

export const fmtMetric = (k: string, v: unknown): string => {
  if (v == null) return '—'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (!isNum(v)) return String(v)
  if (/_rate$|_accuracy$|^hit_at_1$|coverage|precision|recall|exact_day_set/.test(k) && v >= 0 && v <= 1) return `${(v * 100).toFixed(1)}%`
  if (/(_min|_s|_sec|minutes|seconds|^n$|count|routines|users|support|alerts|bookings|arrivals|shows|stays)/.test(k) || Math.abs(v) > 1) return (Math.round(v * 10) / 10).toLocaleString()
  return v.toFixed(3)
}
