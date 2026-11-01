# GoPark Sense — Shared Contract

Every builder (person or Claude session) codes against this file. It defines the data files, database, QR format, API and C2 outputs.
**Changing it:** open a PR that touches only `CONTRACT.md` / `contract/` and get approval from the owner listed below. Never change a shape silently in your own folder.

| Part | Owner | Source of truth |
|---|---|---|
| Ops API, DB, QR, signals, sync | P2 (Ops backend) | §4–§7, `api/schema.sql`, `contract/schemas/*.json` |
| Rhythm outputs (patterns, priors, forecast) | P3 (Rhythm) | §8, `contract/schemas/pattern_row.schema.json` etc. |
| Generator files | P4 (Data) | §3 |
| TypeScript types for apps | P2 + P3 | `contract/types.ts` (mirrors the JSON Schemas) |

---

## 1. Conventions
- **Time:** ISO 8601 strings **with offset**, e.g. `2026-11-16T08:00:00+02:00`. Local timezone is `Asia/Beirut`. The server stores UTC and returns local time with offset.
- **IDs:** strings. Users `U0001`, bookings `B000001`, lots from `config/lots.yaml` (`AUB`, `BDD`, …), devices `dev-…`.
- **Client event IDs:** UUID (v7 preferred, v4 accepted). The server ignores an ID it has already seen (idempotent).
- **Plates:** `plate_display` is as printed (e.g. `B 123456`). `plate_norm` = uppercase, Arabic-Indic digits converted to 0–9, spaces/dashes removed (`B123456`). Lebanese format check: `^[A-Z]\d{1,6}$` (to verify on real plates).
- **Money:** USD, numbers with 2 decimals.
- **Confidence / probability:** number 0–1 (never a percentage string).

## 2. Enums
| Enum | Values |
|---|---|
| `booking.status` | `booked` (upcoming), `completed`, `cancelled`, `no_show` |
| `lot.kind` | `open`, `underground`, `gated`, `valet` |
| `stay.state` | `EXPECTED`, `ARRIVING`, `CHECKED_IN`, `OVERSTAY`, `CHECKED_OUT`, `UNCONFIRMED_DEPARTURE`, `NO_SHOW`, `CANCELLED` |
| `method` (how a state change happened) | `auto`, `driver`, `attendant`, `inferred` |
| `signal.source` | `geofence`, `motion`, `ble`, `nfc`, `gate_qr`, `booking_qr`, `camera_plate`, `camera_spot`, `gate_sensor`, `car_bt`, `attendant`, `driver_confirm` |
| `signal.kind` | `enter`, `exit`, `park`, `unpark`, `scan`, `occupied`, `vacant`, `confirm`, `deny` |
| `alert.type` | `upcoming_arrival`, `overstay`, `unconfirmed_departure`, `no_show`, `unknown_vehicle`, `plate_mismatch`, `low_confidence`, `anomaly` |
| `pattern.state` | `emerging`, `active`, `fading`, `ended` |
| `pattern.pattern_type` | `recurring`, `occasional`, `insufficient_history` |
| `reminder.tier` | `locked_in`, `at_risk`, `emerging`, `none` |

## 3. Generator output files (`data/`)
Produced by `uv run python -m tools.generator` (see README). Everything is seeded and reproducible.

**`bookings.csv`** — C2 input and ops seed. One row per booking.
| column | type | notes |
|---|---|---|
| `booking_id` | str | |
| `user_id` | str | |
| `location_id` | str | lot id |
| `start_time` | ISO time | booked start |
| `end_time` | ISO time | booked end |
| `status` | enum | `booked` only for bookings after the history end |
| `created_at` | ISO time | optional column; engine must work without it |
| `plate` | str | `plate_display`; optional column |

**`truth.json`** — ground truth for evaluation. **Engine code must never read it** except in `rhythm/evaluate`.
```json
{"generated_with": {"seed": 42, "users": 300, "weeks": 26, "history_end": "2026-11-15"},
 "users": {"U0001": {"persona": "mwf_student",
   "routines": [{"days": ["Mon","Wed","Fri"], "center": "08:00", "location_id": "AUB",
                 "active_from": "2026-05-18", "active_to": null}]}}}
```

**`stays.csv`** — what really happened for completed bookings (C1 ground truth, C1→C2 learning link): `booking_id, actual_arrival, actual_departure` (ISO times).

**`traces/<booking_id>.jsonl`** — (from P1 onward) one signal per line in the §6 signal shape, plus `"truth": true|false` marking whether the event really happened. Used by `tools/simulator`.

## 4. Database (SQLite, `api/schema.sql`)
Tables: `lots`, `spaces`, `users`, `vehicles`, `bookings`, `signals`, `stays`, `audit_log`, `alerts`, `points_ledger`. Exact columns live in `api/schema.sql`; only P2 changes it.

## 5. Booking QR format
```
GP1.<payload>.<signature>
payload   = base64url(JSON, no padding): {"bid":"B000123","lot":"AUB","ph":"<sha256(plate_norm)[:16]>","s":"<start ISO>","e":"<end ISO>","n":"<8-char nonce>"}
signature = base64url(Ed25519 signature over the ASCII bytes "GP1.<payload>")
```
- `GET /bookings/{booking_id}/qr` returns `{"qr":"GP1.<payload>.<signature>"}`. The signing key is generated once and stored under the gitignored `keys/` directory; it must not be regenerated while a server is running.
- The public key is served at `GET /keys/qr` and cached by the attendant app, so QR codes can be checked **offline**.
- Offline check order: signature valid → lot matches this attendant's lot → now within `[s − 30 min, e]` → `bid` not already used on this device.
- Rotating gate code (driver scans a screen at the gate): `GPG1.<lot>.<code>`, where `code = first 8 hex chars of HMAC-SHA256(gate_secret, floor(unix_time / 30))`. Valid within ±1 window of the server's receive time.
- `GET /dev/gate/{lot_id}` is demo-only and returns the current `GPG1.<lot>.<code>` string with a local-time `valid_until` timestamp. Gate codes are accepted as `gate_qr` signal payloads and are never booking QR signatures.

## 5.1 Walk-ins, alerts and evidence
- `POST /walk_ins` takes `{lot_id, plate, device_id, client_event_id}` (optional `observed_at` and `space_id`) and returns a normal `scan_result`; its stay has `booking_id: null`.
- `POST /alerts/{alert_id}/resolve` takes `{device_id, note?}`. Resolved alerts remain in storage for audit/sync, but are omitted from the default open-alert list.
- `GET /stays/{stay_id}/evidence` returns `{stay_id, prior, evidence}`. Each evidence row records arrival/departure direction, source signal, observation time, log-likelihood contribution and resulting probability, in chronological order. Signals are preserved as raw append-only records.

## 6. Signals (driver app, simulator, cameras → server)
Schema: `contract/schemas/signal.schema.json`.
```json
{"client_event_id": "0193…", "device_id": "dev-android-1", "user_id": "U0001",
 "lot_id": "AUB", "booking_id": "B000123", "source": "motion", "kind": "park",
 "observed_at": "2026-11-16T08:01:10+02:00",
 "payload": {"accuracy_m": 18, "rssi": -71, "plate_norm": "B123456", "space_id": "LAU-07"}}
```
`user_id`, `booking_id` and `payload` fields are optional; cameras send `plate_norm` and/or `space_id` with no user.
- **Camera attribution (P2).** `camera_plate` is matched to a booking at that lot by `payload.plate_norm` (normalized; exact, or one misread character when `payload.ocr_conf < 0.8` and only one booked plate fits), within 90 min before start to 180 min after end. `enter` prefers a booking without a stay and the nearest start, `exit` one with an open stay and the nearest end. `camera_spot` (`occupied`/`vacant`) is matched by `payload.space_id` to the booking holding that space and only adds occupancy evidence. No `booking_id` is needed from cameras.
- **Gate presence sensor (P2).** Each gate lane has a pair of presence sensors (IR break-beam or ultrasonic, ~1 m apart, on an ESP32); the trigger order gives the direction. It posts `{"source": "gate_sensor", "kind": "enter"|"exit", "device_id": "gate-AUB-1", "lot_id": "AUB", "payload": {"gate_id": "AUB-1", "lane": 1}}` with no user, booking or plate: it says *a car moved*, the lane's `camera_plate` says *who*.
  - **Paired:** a `gate_sensor` event and a `camera_plate` read at the same lot with the same direction within `gate_pairing.window_s` (8 s) are one car; the sensor event then counts for the plate's booking with `signals.gate_sensor.log_lr` (pairing bonus, 2.0) on top of the plate read. Opposite directions never pair. A plate read without a sensor event counts as before.
  - **Unidentified:** a sensor event still unpaired after the window (checked by the scheduler / `POST /dev/tick`). Entry → a booking-less stay `G-<signal id>` (`CHECKED_IN`, `inferred: true`, counted in occupancy) + `low_confidence` alert "Car in … identity unknown" (with `stay_id`). Exit → closes the oldest open booking-less stay at that lot, else a `low_confidence` alert "Car out … identity unknown" (no stay) and the overview's `occupied` drops by one for it (24 h). A plate read with no booking pairs into the same kind of stay with its `plate_norm` and an `unknown_vehicle` alert.
  - **Resolved later:** a plate upload arriving late still pairs (the placeholder stay goes to `CANCELLED`, its alert resolves). A booked stay that starts (fusion, attendant scan, walk-in) within `gate_pairing.resolve_window_min` (15 min) of an unidentified entry claims it the same way; a checkout (evidence, attendant, inferred) claims the nearest unidentified exit, and a silent departure uses that exit's time as `out_at`.
- **Fusion (P2, `api/ops/fusion.py`, weights in `config/fusion.yaml`).** `logit P = logit(prior) + Σ log_lr · 0.5^(age/half_life)`; prior = C2 arrival prior for the window containing now, else `default_prior`. Arrival: P ≥ 0.95 → stay `CHECKED_IN` (`in_method auto`); 0.60–0.95 → `ARRIVING`; no stay and P < 0.60 ten minutes after the first evidence → `low_confidence` alert. Strong evidence (plate, NFC, QR, attendant, driver) at the booked lot **and** another lot → `anomaly` alert (critical), never auto-confirmed. Departure uses exit-like signals after the arrival (`exit`, `unpark`, `vacant`): P ≥ 0.95 → `CHECKED_OUT` (`out_method auto`, `out_at` = first exit signal of the last burst). Strong arrival evidence at another lot closes the open stay (`out_method inferred`, `inferred: true`). Booking ended and silent → `UNCONFIRMED_DEPARTURE` (+ alert), then 30 min later an inferred checkout at the last exit-like evidence, else the booking end (delayed while the spot camera still reports `occupied`); a later confident exit corrects `out_at` (`inferred: false`).

## 7. Ops API (FastAPI, P2)
Base URL `http://<laptop-ip>:8000`. JSON unless noted. Status column says what exists **now**.

| Method & path | Purpose | Status |
|---|---|---|
| `GET /health` | `{ok, now, version}` | ✅ P0 |
| `GET /lots` | List lots (from DB) | ✅ P0 |
| `GET /lots/{lot_id}/overview` | Counts + expected arrivals; schema `lot_overview`; `occupied` includes unidentified gate entries and excludes unclaimed gate exits (§6) | ✅ P0 (counts from bookings; expected list without C2 yet) |
| `POST /scan` | Body `{lot_id, device_id, client_event_id, qr? , booking_id?, plate?}` → `scan_result` | ✅ P0 (booking_id + plate; QR in P2) |
| `POST /stays/{stay_id}/checkout` | Body `{device_id, client_event_id, method}` | ✅ P0 |
| `GET /stays?lot_id=&state=` | Current stays | ✅ P0 |
| `GET /bookings/{booking_id}/qr` | `{qr}` signed booking QR string | 🟡 P1 |
| `POST /walk_ins` | `{lot_id, plate, device_id, client_event_id}` → `scan_result` | 🟡 P1 |
| `GET /stays/{stay_id}/evidence` | Fused-confidence evidence timeline | 🟡 P1/P2 |
| `GET /audit?lot_id=&limit=` | Activity history | ✅ P0 |
| `POST /signals` | Body = signal, or `{"signals":[…]}` → `{accepted, duplicates}`; fuses the affected bookings (§6) | ✅ P2 |
| `POST /sync/push` | `{device_id, events:[sync_event]}` → `{acks:[id], conflicts:[{id, reason}]}` | 🟡 stub (P2) |
| `GET /sync/pull?lot_id=&since=` | Changed bookings/stays/alerts since a cursor | 🟡 stub (P2) |
| `GET /alerts?lot_id=` | Open alerts | 🟡 P0 computes; P1 persists |
| `POST /alerts/{alert_id}/resolve` | `{device_id, note?}` resolves an alert | 🟡 P1 |
| `POST /stays/{stay_id}/confirm` | Driver one-tap `{answer: "yes"|"no"}` | 🟡 stub (P3) |
| `GET /users/{user_id}/rewards` | Points, grace minutes, tier | 🟡 stub (P3) |
| `GET /keys/qr` | `{public_key}` base64url Ed25519 public key | 🟡 stub (P2) |
| `GET /dev/gate/{lot_id}` | `{code, valid_until}` rotating gate QR (demo only) | 🟡 P1 |
| `WS /live?lot_id=` | Pushes `{type: "stay"|"alert", data}` on every change (see below) | ✅ P2 |
| `POST /dev/reset?prepark=` | Reload DB from `data/` (demo only); `prepark=false` skips the cars parked at 07:50 | ✅ P0 / P2 |
| `POST /dev/tick` | Run the time-based rules now (alerts, silent departures, low confidence) → `{now, rescored}` | ✅ P2 (demo/simulator) |
| `GET /dev/decisions?since=&until=` | Per booking (start in range): `{booking_id, lot_id, status, stay, arriving_at, confirmed_at, checked_out_at, check_out_action, alerts:[type]}` — decision times on the demo clock | ✅ P2 (simulator) |

`scan_result`:
```json
{"result": "checked_in" | "checked_out" | "already_in" | "wrong_lot" | "too_early" | "expired" | "not_found" | "invalid_qr",
 "message": "Checked in B 123456 for AUB until 12:00",
 "booking": { …booking… } | null, "stay": { …stay… } | null, "suggest": "register_walk_in" | null}
```

`WS /live?lot_id=` (lot optional = all lots): after connect the server sends one JSON message per stay or alert that is inserted or meaningfully changed (stay state/times/confidence; alert status/severity/message/fee), whatever caused it (scan, signals, sync, scheduler). Shape `{"type": "stay", "data": <stay as in GET /stays>}` or `{"type": "alert", "data": <alert as in GET /alerts>}`; only the latest state of an entity is sent per 0.5 s poll. Nothing is replayed on connect: load `GET /stays` / `GET /alerts` first, then apply messages. Clients may send any text (ignored); keep polling as a fallback.

`runs/fusion_metrics.json` (`python -m tools.simulator evaluate [--tune]`, simulator replay of `data/traces/` vs `traces/index.csv` truth):
- top level: `generated_at`, `mode` (`replay` through the API or `offline` pure fusion), `traces`, `gate_sensors` (true when the simulator synthesized `gate_sensor` events next to the plate reads), `bookings`, `priors` (C2 priors available), `split {method, tune, test}` (sha1(booking_id) halves; tuning only sees `tune`), `objective` (text), `config_before`, `config_after` (null without `--tune`), `tuning {changed: {path: [old, new]}, steps, objective_tune_before/after, objective_test_before/after}`, `before`, `after`.
- `before` / `after` = `{all, tune, test, by_scenario: {clean|weak_gps|underground|phone_dead|contradiction|no_show: M}, by_lot_kind: {open|gated|underground|valet: M}}`.
- `M`: `bookings`, `arrivals` (cars that came), `no_shows`; over arrivals: `auto_confirm_rate` (auto CHECKED_IN), `ask_driver_rate` (only ever ARRIVING), `missed_arrival_rate` (no stay), `early_confirm_rate` (confirmed > 10 min before the real arrival), `median_time_to_confirm_min` / `p90_time_to_confirm_min` (decision time − real arrival); over no-shows: `false_confirm_rate`, `false_ask_rate`; `low_confidence_alerts`, `anomaly_alerts` (bookings with one); `false_evidence_confirms` (auto-confirms that the `truth: true` signals alone would not have made); `departure {stays, auto_checkout_rate, moved_checkout_rate, inferred_checkout_rate, still_open_rate, early_checkout_rate, median_time_to_checkout_min, median_out_at_error_min, p90_out_at_error_min}`. Rates are 0–1 or null when the group is empty.

`sync_event` (attendant app outbox): `{client_event_id, type: "scan"|"checkout"|"walk_in"|"resolve_alert"|"override", lot_id, observed_at, body:{…same as the online endpoint body…}}`.

## 8. Rhythm (C2) outputs
`uv run python -m rhythm run --input data/bookings.csv --out runs/` writes `runs/<run_id>/`:

| File | Schema | Notes |
|---|---|---|
| `patterns.json` | array of `pattern_row` | One primary row per user (`is_primary: true`) + one per extra routine |
| `patterns.csv` | flat version | Nested fields flattened with `_` (e.g. `next_booking_at`, `reminder_tier`) |
| `arrival_priors.json` | array of `arrival_prior` | Consumed by C1 fusion (P2) |
| `lot_forecast.json` | array of `lot_forecast` | Consumed by the attendant "expected soon" strip + dashboard |
| `run.json` | `{run_id, created_at, cutoff, input, model_version, params, calibration, counts}` | `calibration`: `{x, y, n, fitted, tau, fitted_on}` (isotonic knots + reminder τ); `counts`: `users, rows, recurring, states{emerging,active,fading,ended}, reminders, priors` |
| `metrics.json` | keys below | From `rhythm evaluate` |

`runs/latest.txt` holds the newest `run_id`.

**Pattern-row semantics (phase P2, `model_version` `rhythm-2.0-p2`).** Shapes are unchanged.
- `state`: `emerging` (new routine or < 4 weeks seen), `active`, `fading` (dropped but still on), `ended` (stopped after a detected change; kept so switches can be shown). `null` for occasional / insufficient rows. Rows of one user: current routines first (strongest = primary), ended ones after; a user with only ended routines has an ended primary row.
- `change_date`: date of the latest detected change of that routine (start for emerging, drop for fading/ended, last upward change for active, else `null`).
- `confidence`: **calibrated** probability of `next_booking` (isotonic map fitted on the 4 weeks before the cutoff). `recurrence_prob`: posterior mean weekly rate on the routine's days (not calibrated). Ended rows: `next_booking` null, `confidence` 0.
- `reminder.tier`: `locked_in` (active, p ≥ 0.9, or p ≥ τ with ≥ 6 booked slots in a row; `recommend` false), `emerging` (new routine, p ≥ 0.3), `at_risk` (p ≥ τ otherwise), `none`. `recommend` is true only for `at_risk` / `emerging` when the slot isn't already booked and no other reminder for that user falls within 48 h. `reminder.at`: median `created_at` lead + 30 min before the slot (fallback: 20:00 the evening before morning slots, else 2 h before), never before the cutoff day 20:00.
- `reason`: template sentence with real counts; covers active / emerging / fading / ended / routine switch / occasional (incl. "likely within 7 days at X, ~N%") / insufficient history, plus inactivity and reminder notes.
- `arrival_priors.p` and `lot_forecast.expected_arrivals` use calibrated probabilities; the forecast adds a non-routine base rate (bookings outside routines at that lot × weekday × hour over the last 8 weeks ÷ 8).

**`metrics.json` keys** (`GET /rhythm/runs/{run_id}/evaluation`). Splits: `validation` and `test` (4 weekly cutoffs each, counted back from the last finished week); calibration and τ are fitted on `validation` only.
| Key | Content |
|---|---|
| `run_id, input, end, model_version, settings{window_days, hit_tolerance_minutes}, definitions` | Context; `definitions` = plain-text metric definitions |
| `cutoffs.{validation,test}` | ISO dates (each = last training day; predicts the next 7 days) |
| `next_booking.{split}.{engine,B1,B2,B3}` | `hit_at_1, time_mae_min, coverage, n, slot_precision, slot_recall, slot_f1` |
| `by_cutoff.{split}.{model}` | list of the same summary + `cutoff` |
| `by_persona.{split}.{model}.{persona}` | `hit_at_1, n` |
| `calibration` | `map{x,y}, fitted, fitted_on, n`, and `{validation,test}.{before,after}` = `{brier, ece, n, bins:[{lo, hi, mean_pred, observed, n}]}` |
| `reminders` | `target_precision, tau`, `{validation,test}`: `[{tau, precision, recall, reminders}]` for τ = 0.05…0.95, `{split}_at_tau` |
| `forecast.{engine,B2}` | `mae_per_lot_hour, rmse_per_lot_hour, bias_per_lot_hour, cells, actual_arrivals, expected_arrivals` (test weeks) |
| `recovery.{engine,B2}`, `recovery_validation.{cutoff, engine, B2}` | `{overall, by_persona.{persona}}` = `routines, day_jaccard, exact_day_set, lot_accuracy, time_err_min, users_without_routine, false_habit_rate` (needs `truth.json`) |
| `false_habit.{engine,B2}` | `{persona: rate, overall}` for users with no true routine (incl. `coincidence_trap`) |
| `drift` | `cutoffs`, `{engine,B2}.summary.{started,ended}` = `events, detected, median_delay_days, mean_delay_days, median_change_date_error_days`; `{model}.events` = per-event rows; `states_at_end.{persona}.{state}` = count |
| `ablation` | `[{variant: full|-prior|-decay|-drift|-activity|-calibration, hit_at_1, time_mae_min, slot_f1, brier, ece, tau, reminder_precision, reminder_recall, day_jaccard, false_habit_rate}]` |
| `charts` | Chart-ready: `headline` (hit_at_1, slot_f1, brier/ece before-after, reminder_at_tau, day_jaccard, false_habit_rate), `hit_at_1_by_cutoff{cutoffs, split, series.{model}}`, `reliability{before, after}` (bins), `reminder_curve{tau, target_precision, points}`, `forecast[]`, `persona_table[]`, `drift_delay[]`, `ablation[]` |

`recovery`, `false_habit`, `drift` and the recovery columns of `ablation` exist only when `truth.json` is next to the input; `ablation` is skipped with `--no-ablation`.

**Rhythm API** (mounted in the same FastAPI app):
| Method & path | Purpose | Status |
|---|---|---|
| `POST /rhythm/runs` | `{input?: path, cutoff?: date}` → `run.json` | ✅ P0 |
| `GET /rhythm/runs/latest` | `run.json` of the newest run | ✅ P0 |
| `GET /rhythm/runs/{run_id}/patterns?format=json|csv&user_id=&state=&min_conf=&primary_only=` | Pattern table | ✅ P0 |
| `GET /rhythm/runs/{run_id}/priors?lot_id=&date=` | Arrival priors | ✅ P0 (stub data from baseline) |
| `GET /rhythm/runs/{run_id}/forecast?lot_id=&date=` | Lot forecast | ✅ P0 (stub data from baseline) |
| `GET /rhythm/users/{user_id}` | Routines + booking history + weekday×hour heatmap | ✅ P0 (history + heatmap) |
| `GET /rhythm/runs/{run_id}/evaluation` | `metrics.json` (keys above) | ✅ P2 |
| `POST /rhythm/datasets` | Upload CSV (multipart) → `{path}` | 🟡 stub (P3/P4) |

`run_id` may be `latest` in any path.

## 9. TypeScript types
`contract/types.ts` mirrors the schemas for the dashboard and mobile app. Each app copies it with `npm run contract` (copies to `src/contract.ts`). Don't edit the copies.
