# Brief P4 — Data generator + Dashboard + Slides

**Branch:** `feat/data-dash` · **You own:** `tools/generator/`, `dashboard/`, `docker-compose.yml`, `slides/`
**Read:** `CONTRACT.md` §3 (you own it), §7–§8 (you consume them); `ARCHITECTURE_PLAN.md` §5, §8 and §12

## Mission
1. Produce realistic, hard synthetic data **with ground truth**, independently of the engine, so its evaluation is believable.
2. Build the dashboard the judges watch: live lots, detection evidence, pattern table, user drill-down, evaluation.
3. Own the demo materials: slides, backup video, one-command startup.

## Independence rule
You write the generator **without reading `rhythm/`**. You also own the two **hidden personas**: keep them out of git until the final evaluation (put them in `tools/generator/hidden_personas.py` and add that file to `.gitignore` on your branch). Then hand P3 a dataset that includes them.

## Where it stands (P0)
- `tools/generator/`:
  - `personas.py` has 3 personas (`weekday_commuter`, `mwf_student`, `occasional`) in a `PERSONAS` registry;
  - the calendar honours holidays and term breaks;
  - start times rounded to 5 min, durations to 15 min.
- `__main__.py` writes:
  - `bookings.csv`, with `created_at` and `plate`, plus a "live" week after 2026-11-15 with status `booked`;
  - `truth.json`;
  - `stays.csv` (actual arrival/departure).
- **Known gap:** the generator ignores lot capacity, so LAU and AUB can be over-subscribed at 08:00.
- `dashboard/` (React 19 + Vite 8 + TS):
  - `LiveLots.tsx`: counts, spot grid, expected soon, alerts, activity, parked cars; polls every 5 s;
  - `Patterns.tsx`: table, search, CSV/JSON export;
  - hash tabs in `App.tsx`; `/api` proxied to :8000.

## Run
```bash
uv run python -m tools.generator --users 300 --weeks 26 --seed 42
```
```bash
npm --prefix dashboard run dev
```
(API: `uv run uvicorn api.main:app --reload --port 8000`; rerun `uv run python -m rhythm run` after regenerating data, then `POST /dev/reset`.)

## Phase P1 (hours 3–12): full generator
- [ ] **Personas** (ARCHITECTURE_PLAN §5.1): add each with `truth.json` routines carrying `active_from` / `active_to`:
  - `routine_switcher` (changes days/lot mid-period; truth has two routines with the switch date)
  - `fader` (attendance probability decays over weeks)
  - `noisy_timer` (±40–60 min jitter)
  - `heavy_canceller` (30–50% cancels, some cancel + rebook)
  - `multi_routine` (weekdays office + Sat Hamra)
  - `coincidence_trap` (random, but lands on one weekday 3–4 times)
  - `new_user` (1–3 weeks of history)
  - `lapsed` (strong routine, then inactive)
  - 2 hidden personas (see the independence rule)
- [ ] **Capacity:** drop or shift bookings that would exceed a lot's capacity at their start time.
- [ ] **`--messy` flag:** duplicates, status case noise (`Completed`, ` cancelled `), a few missing `end_time`, a few end-before-start, a few timestamps without an offset. Log how many of each were injected.
- [ ] **Driver traces** `data/traces/<booking_id>.jsonl` for live-week bookings, in the `signal` shape plus `"truth": true|false`. Scenarios, chosen by lot kind:

  | Scenario | Signals |
  |---|---|
  | Clean open-air | geofence enter → motion park → (exit later) |
  | Weak GPS | geofence enter with large `accuracy_m`, no motion |
  | Underground | no GPS; `ble` or `camera_plate` at the ramp; phone offline, signals sent later with the original `observed_at` |
  | Phone dead | no driver signals (attendant scan needed) |
  | Contradiction | geofence at the wrong lot |

  Include `camera_plate` / `camera_spot` signals for lots with cameras, so camera-first works without code changes. Agree the file format with P2 before writing lots of it.

**Done when:** P3 can run on the new data, and P2's simulator can replay traces.

## Phase P2 (hours 12–24): dashboard pages
- [ ] **Design pass:** tokens in `index.css` (GoPark green, both themes), proper type scale; install `echarts` (+ a thin React wrapper or direct `echarts.init`).
- [ ] **Live lots:** add an expected-arrivals curve (`/rhythm/runs/latest/forecast?lot_id=&date=`) vs actual check-ins per hour, and method badges. Use `WS /live` when P2 ships it; keep polling as a fallback.
- [ ] **Detection log:** list of today's stays; clicking one shows `/stays/{id}/evidence` (from P2) as a timeline of signals with the fused-confidence line crossing the 0.60 and 0.95 thresholds. This is how judges *see* fusion work.
- [ ] **Pattern table:** filters by state, tier, lot and confidence; sort; reminder tier pills; "all routines" toggle; export.
- [ ] **User drill-down** (`/rhythm/users/{id}`): weekday × hour heatmap, booking timeline (completed / cancelled / no-show marks) with routine bands and change-date markers, next prediction + reminder + reason.

## Phase P3 (hours 24–32): evaluation page + hidden personas
- [ ] **Evaluation page** from `/rhythm/runs/latest/evaluation` (agree keys with P3):
  - Hit@1 vs baselines (bars);
  - reliability diagram before/after calibration;
  - precision/recall vs τ with a slider;
  - ablation table;
  - per-persona breakdown;
  - hidden-persona results shown separately;
  - C1 detection numbers (auto-confirm rate, false-confirm rate, time to confirm) from P2 / field test.
- [ ] **Hidden personas:** generate the final dataset including them, hand it to P3, keep the definitions private until then.

## Phase P4 (hours 32–40): demo materials
- [ ] **`docker-compose.yml`:** API + built dashboard + pre-generated data, so `docker compose up` gives a working demo.
- [ ] **Slides** (`slides/`, 3–4 slides):
  1. architecture loop;
  2. accuracy (C2 metrics + C1 field/sim numbers) and cost per lot kit;
  3. incentives;
  4. next steps.
- [ ] **Backup video** of the full demo script (ARCHITECTURE_PLAN §12).

## Interfaces
| You provide | To |
|---|---|
| `bookings.csv`, `truth.json`, `stays.csv` (shape fixed by CONTRACT §3) | P3, P2 |
| `data/traces/*.jsonl` | P2 simulator |
| Hidden-persona dataset | P3 (phase P3) |

| You consume | From |
|---|---|
| Evidence endpoint, `/live` WebSocket | P2 |
| `metrics.json` keys, forecast, user rhythm | P3 |

## Gotchas
- Changing the generator's output columns is a contract change. Adding personas or noise is not.
- Keep `--seed` deterministic (a test checks it).
- The dashboard must show the **demo clock** (from the API), not the browser clock.
