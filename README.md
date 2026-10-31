# GoPark Sense

GoPark × AUB hackathon: one system for both challenges.
- **Challenge 1:** attendant app + automatic arrival/departure detection.
- **Challenge 2:** booking pattern engine ("Rhythm").

Read these first:
- [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) — what we're building and why.
- [CONTRACT.md](CONTRACT.md) — the shared data formats and API every part codes against.

## Run it

Needs Python 3.12+ with [uv](https://docs.astral.sh/uv/) and Node 22+.

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
