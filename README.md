# GoPark Sense

GoPark × AUB hackathon: one system for both challenges.
- **Challenge 1:** attendant app + automatic arrival/departure detection.
- **Challenge 2:** booking pattern engine ("Rhythm").

Read these first:
- [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) — what we're building and why.
- [CONTRACT.md](CONTRACT.md) — the shared data formats and API every part codes against.

## Run it

Needs Python 3.12+ with [uv](https://docs.astral.sh/uv/) and Node 22+.

**One-command demo** (data, Rhythm, API on :8000, dashboard on :5173, then a scripted morning with narration; script in [docs/DEMO.md](docs/DEMO.md)):
```bash
uv run python -m tools.demo
```

Or step by step:

```bash
uv sync
```
```bash
uv run python -m tools.generator
```
```bash
uv run python -m rhythm run
```
```bash
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```
```bash
npm --prefix dashboard install
```
```bash
npm --prefix dashboard run dev
```

- The dashboard runs at http://localhost:5173 and the API docs at http://localhost:8000/docs.
- The API seeds `data/gopark.sqlite` from `data/bookings.csv` on first start. `POST /dev/reset` reloads it.
- **Demo clock:** starts Monday 2026-11-16 07:50 (Beirut) and keeps ticking. `POST /dev/clock {"advance_minutes": 120}` fast-forwards it.

**Mobile app** (Expo):
1. Put the laptop's LAN address in `apps/mobile/.env`, e.g. `EXPO_PUBLIC_API_URL=http://192.168.1.20:8000`.
2. Run the command below, then scan the QR code with Expo Go.

```bash
npm --prefix apps/mobile run start
```

**Tests** (contract checks: every producer's output matches `contract/schemas`):
```bash
uv run pytest
```

## Integrating a gate sensor
A gate lane has a pair of presence sensors (IR break-beam or ultrasonic, ~1 m apart, on an ESP32). The order in which the two beams break gives the direction (A then B = `enter`, B then A = `exit`). The sensor says *a car passed*; the lane's plate camera says *who*. The full rules are in [CONTRACT.md](CONTRACT.md) §6 ("Gate presence sensor").

**Send one signal per car that passes**: `POST /signals` with a JSON body, either one signal or `{"signals": [...]}` for a batch.

| Field | Value |
|---|---|
| `client_event_id` | Unique per event (≥ 8 chars), e.g. a UUID. Retries with the same id are ignored, so resending after a timeout is safe. |
| `device_id` | `gate-<LOT>-<n>`, e.g. `gate-AUB-1` |
| `lot_id` | One of `GET /lots`: `AUB`, `BDD`, `LAU`, `HAMRA`, `VERDUN`, `ACHRAFIEH` |
| `source` / `kind` | `gate_sensor` / `enter` or `exit` |
| `observed_at` | When the second beam broke, ISO 8601 with offset |
| `payload` | `{"gate_id": "AUB-1", "lane": 1}` (`lane` optional) |
| `user_id`, `booking_id` | Leave them out: the sensor doesn't know who it is |

Use the API's demo clock for `observed_at` while testing. It starts on Monday 2026-11-16 07:50 Beirut time, and events far from it look old. Either read it with `GET /dev/clock`, or move it to real time with `POST /dev/clock {"now": "<your ISO time>"}`.

```bash
curl -s http://localhost:8000/dev/clock
```
```bash
curl -s -X POST http://localhost:8000/signals -H "Content-Type: application/json" -d '{"client_event_id": "gate-AUB-1-000001", "device_id": "gate-AUB-1", "lot_id": "AUB", "source": "gate_sensor", "kind": "enter", "observed_at": "2026-11-16T07:51:00+02:00", "payload": {"gate_id": "AUB-1", "lane": 1}}'
```
Expected response, `200`:
```json
{"accepted": 1, "duplicates": 0}
```
Sending the same `client_event_id` again gives `{"accepted": 0, "duplicates": 1}`. A bad `source`, `kind` or missing field gives `422` with the reason.

**What happens next:**
- **A plate read of the same direction within 8 s** (`camera_plate` at the same lot) pairs with the event: one identified car, and the booking's stay goes `CHECKED_IN` / `CHECKED_OUT` with strong evidence.
- **No plate read:** after the window, the next scheduler pass (every 30 s, or immediately with `POST /dev/tick`) does one of two things:
  - an `enter` becomes an unknown-car stay `G-<id>` plus a `low_confidence` alert;
  - an `exit` closes an unknown-car stay at that lot if there is one; otherwise it raises an alert and the lot's `occupied` count goes down by one.

Check with:
```bash
curl -s -X POST http://localhost:8000/dev/tick
```
```bash
curl -s http://localhost:8000/lots/AUB/overview
```
```bash
curl -s "http://localhost:8000/alerts?lot_id=AUB"
```
Live updates: `WS /live?lot_id=AUB` pushes every stay/alert change as `{"type": "stay"|"alert", "data": {...}}`.

## What works now (phase P0)
| Part | Status |
|---|---|
| Generator | 3 personas (weekday commuter, MWF student, occasional), holidays and term breaks, `created_at`, plates, actual stays, plus 1 upcoming "live" week |
| Rhythm | Baseline B2 (modal weekdays/time/lot) writing contract-valid patterns, arrival priors and lot forecast; CSV/JSON export |
| API | Lots, overview with "expected soon" (bookings + habit predictions), scan by booking ID or plate (check-in/out, wrong lot, too early, expired, walk-in suggestion, double-scan guard, idempotent retries), stays, audit log, signal storage, basic offline sync replay, on-the-fly overstay/upcoming alerts, demo clock |
| Dashboard | Live lot view (counts, spot grid, expected soon, alerts, activity, parked cars) and pattern table with export |
| Mobile | Attendant mode: pick a lot, see counts and expected arrivals, check in/out by booking ID or plate. Driver mode is a placeholder |

Not built yet: evidence fusion, QR signing and camera scanning, offline outbox in the app, driver mode, rewards, the full Rhythm engine, evaluation, simulator. See the phases in ARCHITECTURE_PLAN.md §10.

## Workstreams (one person and one Claude Code session each)
Full briefs and the kickoff prompt for each Claude session: [docs/briefs/](docs/briefs/README.md).

| Owner | Folders | First tasks (phase P1) |
|---|---|---|
| P1 Mobile | `apps/mobile/` | Expo Router screens; camera QR + plate entry; overview; history; start the offline outbox (`expo-sqlite`) |
| P2 Ops backend | `api/ops/`, `api/schema.sql`, `tools/simulator/` | Signed QR (`/keys/qr`), sync cursor + anomaly queue, persisted alerts, fusion from `config/fusion.yaml`, WebSocket `/live` |
| P3 Rhythm | `rhythm/`, `api/rhythm_api/` | Replace `rhythm/pipeline._patterns` with the real pipeline (§4.2); `rhythm evaluate` with baselines; keep output shapes |
| P4 Data + Dashboard | `tools/generator/`, `dashboard/` | Remaining personas (switcher, fader, noisy, canceller, coincidence trap, multi-routine, new, lapsed, 2 hidden), messy-row injector, driver traces; dashboard design + drill-down page |

Rules: stay in your folders, change shapes only through a `CONTRACT.md` PR, run `uv run pytest` before merging, and merge to `main` every ~4 h.
