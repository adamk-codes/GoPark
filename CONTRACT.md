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
| `signal.source` | `geofence`, `motion`, `ble`, `nfc`, `gate_qr`, `booking_qr`, `camera_plate`, `camera_spot`, `car_bt`, `attendant`, `driver_confirm` |
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
- `GET /stays/{stay_id}/evidence` returns `{stay_id, prior, evidence}`. Each evidence row records its source signal, observation time, log-likelihood contribution and resulting probability, in chronological order. Signals are preserved as raw append-only records.

## 6. Signals (driver app, simulator, cameras → server)
Schema: `contract/schemas/signal.schema.json`.
```json
{"client_event_id": "0193…", "device_id": "dev-android-1", "user_id": "U0001",
 "lot_id": "AUB", "booking_id": "B000123", "source": "motion", "kind": "park",
 "observed_at": "2026-11-16T08:01:10+02:00",
 "payload": {"accuracy_m": 18, "rssi": -71, "plate_norm": "B123456", "space_id": "LAU-07"}}
```
`user_id`, `booking_id` and `payload` fields are optional; cameras send `plate_norm` and/or `space_id` with no user.

## 7. Ops API (FastAPI, P2)
Base URL `http://<laptop-ip>:8000`. JSON unless noted. Status column says what exists **now**.

| Method & path | Purpose | Status |
|---|---|---|
| `GET /health` | `{ok, now, version}` | ✅ P0 |
| `GET /lots` | List lots (from DB) | ✅ P0 |
| `GET /lots/{lot_id}/overview` | Counts + expected arrivals; schema `lot_overview` | ✅ P0 (counts from bookings; expected list without C2 yet) |
| `POST /scan` | Body `{lot_id, device_id, client_event_id, qr? , booking_id?, plate?}` → `scan_result` | ✅ P0 (booking_id + plate; QR in P2) |
| `POST /stays/{stay_id}/checkout` | Body `{device_id, client_event_id, method}` | ✅ P0 |
| `GET /stays?lot_id=&state=` | Current stays | ✅ P0 |
| `GET /bookings/{booking_id}/qr` | `{qr}` signed booking QR string | 🟡 P1 |
| `POST /walk_ins` | `{lot_id, plate, device_id, client_event_id}` → `scan_result` | 🟡 P1 |
| `GET /stays/{stay_id}/evidence` | Fused-confidence evidence timeline | 🟡 P1/P2 |
| `GET /audit?lot_id=&limit=` | Activity history | ✅ P0 |
| `POST /signals` | Body = signal, or `{"signals":[…]}` → `{accepted, duplicates}` | ✅ P0 stores only; fusion in P2 |
| `POST /sync/push` | `{device_id, events:[sync_event]}` → `{acks:[id], conflicts:[{id, reason}]}` | 🟡 stub (P2) |
| `GET /sync/pull?lot_id=&since=` | Changed bookings/stays/alerts since a cursor | 🟡 stub (P2) |
| `GET /alerts?lot_id=` | Open alerts | 🟡 P0 computes; P1 persists |
| `POST /alerts/{alert_id}/resolve` | `{device_id, note?}` resolves an alert | 🟡 P1 |
| `POST /stays/{stay_id}/confirm` | Driver one-tap `{answer: "yes"|"no"}` | 🟡 stub (P3) |
| `GET /users/{user_id}/rewards` | Points, grace minutes, tier | 🟡 stub (P3) |
| `GET /keys/qr` | `{public_key}` base64url Ed25519 public key | 🟡 stub (P2) |
| `GET /dev/gate/{lot_id}` | `{code, valid_until}` rotating gate QR (demo only) | 🟡 P1 |
| `WS /live?lot_id=` | Pushes `{type: "stay"|"alert", data}` | 🟡 not yet (P2) |
| `POST /dev/reset` | Reload DB from `data/` (demo only) | ✅ P0 |

`scan_result`:
```json
{"result": "checked_in" | "checked_out" | "already_in" | "wrong_lot" | "too_early" | "expired" | "not_found" | "invalid_qr",
 "message": "Checked in B 123456 for AUB until 12:00",
 "booking": { …booking… } | null, "stay": { …stay… } | null, "suggest": "register_walk_in" | null}
```

`sync_event` (attendant app outbox): `{client_event_id, type: "scan"|"checkout"|"walk_in"|"resolve_alert"|"override", lot_id, observed_at, body:{…same as the online endpoint body…}}`.

## 8. Rhythm (C2) outputs
`uv run python -m rhythm run --input data/bookings.csv --out runs/` writes `runs/<run_id>/`:

| File | Schema | Notes |
|---|---|---|
| `patterns.json` | array of `pattern_row` | One primary row per user (`is_primary: true`) + one per extra routine |
| `patterns.csv` | flat version | Nested fields flattened with `_` (e.g. `next_booking_at`, `reminder_tier`) |
| `arrival_priors.json` | array of `arrival_prior` | Consumed by C1 fusion (P2) |
| `lot_forecast.json` | array of `lot_forecast` | Consumed by the attendant "expected soon" strip + dashboard |
| `run.json` | `{run_id, created_at, cutoff, input, model_version, params}` | |
| `metrics.json` | free-form (P3 decides) | From `rhythm evaluate` |

`runs/latest.txt` holds the newest `run_id`.

**Rhythm API** (mounted in the same FastAPI app):
| Method & path | Purpose | Status |
|---|---|---|
| `POST /rhythm/runs` | `{input?: path, cutoff?: date}` → `run.json` | ✅ P0 |
| `GET /rhythm/runs/latest` | `run.json` of the newest run | ✅ P0 |
| `GET /rhythm/runs/{run_id}/patterns?format=json|csv&user_id=&state=&min_conf=&primary_only=` | Pattern table | ✅ P0 |
| `GET /rhythm/runs/{run_id}/priors?lot_id=&date=` | Arrival priors | ✅ P0 (stub data from baseline) |
| `GET /rhythm/runs/{run_id}/forecast?lot_id=&date=` | Lot forecast | ✅ P0 (stub data from baseline) |
| `GET /rhythm/users/{user_id}` | Routines + booking history + weekday×hour heatmap | ✅ P0 (history + heatmap) |
| `GET /rhythm/runs/{run_id}/evaluation` | `metrics.json` | 🟡 stub (P3) |
| `POST /rhythm/datasets` | Upload CSV (multipart) → `{path}` | 🟡 stub (P3/P4) |

`run_id` may be `latest` in any path.

## 9. TypeScript types
`contract/types.ts` mirrors the schemas for the dashboard and mobile app. Each app copies it with `npm run contract` (copies to `src/contract.ts`). Don't edit the copies.
