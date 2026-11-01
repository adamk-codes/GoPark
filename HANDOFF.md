# Handoff — current status

> **Read this first** if you're a new Claude Code / Cursor session picking up the work.
> Last updated: 2026-11-01. **Phases 1 and 2 are merged and pushed on `main`.** Checks: `uv run pytest` gives **84 passed**; the dashboard builds; the mobile `tsc` passes.
> - **Ops:**
>   - fusion P2 (camera attribution, departures, anomaly and low-confidence alerts);
>   - `tools/simulator` (replay + evaluate + tune), `WS /live`;
>   - gate-sensor pairing (±8 s), tuned `config/fusion.yaml`.
>   - Replay before tuning: 91% auto-confirm, 3% missed. Tuned on synthetic data, 94% auto-confirm and 0 false confirms (may be overfit).
> - **Rhythm:** drift (CUSUM), relaxed habit test, isotonic calibration, remind tiers, explanations, evaluate v2. Test weeks: Hit@1 **0.718** vs B2 0.684; day-set Jaccard **0.976** vs B2 0.874.
> - **Edge:** plate reader, spot watcher, gate-sensor-triggered plate reading, `edge/sensor_sim.py`, `edge/INTEGRATION.md`. `--trigger api` needs `GET /signals`, which ops hasn't built yet.
> - **Dashboard:** Detection log on real evidence, resolvable alerts, `useLive`, Evaluation tab.
> - **Mobile:** SQLite cache + outbox, offline QR check, alerts resolve/"Needs eyes". Driver confirm cards, Rewards screen, optional background geofence (P3). The confirm/rewards shapes are **assumed** until the ops contract lands. **Never run on a device.**
>
> **Phase 3 in progress** (branches not merged yet):
> - ops (`claude/feat-ops-p2-afe7a2`): `POST /stays/{id}/confirm`, `GET /users/{id}/pending_confirmations`, rewards ledger, README gate-sensor guide. After it lands, mobile swaps the assumed shapes for the real ones.
> - dashboard (`claude/feat-dash-p2-c992b8`): `tools/demo.py` one-command demo + `docs/DEMO.md`.
> - Still to do: `GET /signals` for the edge trigger, field test on real phones, slides.
>
> **The gate-sensor hardware/firmware is owned by an external engineer working in Cursor.** They POST `gate_sensor` signals per CONTRACT §6. We don't write ESP32 code.
> Repo: https://github.com/adamk-codes/GoPark · default branch `main`

## 1. What this project is (30 seconds)
One system for both GoPark × AUB hackathon challenges:
- **C1:** attendant app + automatic arrival/departure detection.
- **C2:** "Rhythm", the booking pattern engine.

C2's predictions feed C1's detection and the "expected soon" list.

Source-of-truth docs:
- `ARCHITECTURE_PLAN.md`: design.
- `CONTRACT.md`: every shared shape and endpoint.
- `docs/briefs/`: one brief per workstream, with phase checklists.
- `README.md`: run commands.

**Decision taken (owner, 2026-11-01):** detection = **gate sensors + plate camera**.
- At each gate, two presence sensors about 1 m apart (IR break-beam or ultrasonic, on an ESP32) catch every car going in or out. The order they trigger gives the direction.
- A camera facing the lane (an old phone) reads the plate, on entry **and** exit, to say whose car it is.
- New signal `gate_sensor` (enter/exit). Fusion pairs it with `camera_plate` within ~±8 s.
- A sensor event with no readable plate still updates occupancy, and is flagged "identity unknown" for the attendant.
- Phone background signals are backup. NFC/QR are optional fallbacks. BLE beacons and the rotating gate-QR screen are dropped.
- Phase 2: the gate-sensor design was sent to the ops session (contract + fusion pairing) and the edge session (ESP32 firmware + sensor-triggered plate reading) mid-run. Check their reports to see if they included it.

## 2. Done (on `main`)
| Area | State | Verified |
|---|---|---|
| Contract | P0 + ops P1 additions: booking QR, walk-ins, alerts with ids/resolve, stay evidence (with `direction`), gate code, QR public key | Type copies in dashboard + mobile in sync |
| Ops API (`api/ops/`) | Scan by QR / booking ID / plate, walk-ins, check-out, Ed25519-signed booking QR, gate code, persisted alerts (30 s scheduler: overstay + fee, unconfirmed departure, no-show, upcoming), sync push/pull with cursor + conflicts, **evidence fusion** (`fusion.py`: signals → ARRIVING / CHECKED_IN / CHECKED_OUT, saved evidence), demo clock | Tests |
| Rhythm (`rhythm/`) | Stages `clean`, `activity`, `discover`, `habit`, `predict`; several routines per user; `python -m rhythm evaluate` vs baselines B1/B2/B3 | Hit@1 **0.707** vs B2 0.684, B1 0.565, B3 0.518 (test weeks) |
| Generator (`tools/generator/`) | 12 personas incl. switcher, fader, noisy, canceller, multi-routine, coincidence trap, new, lapsed; 2 hidden (gitignored, `--include-hidden`); capacity; `--messy`; driver traces (clean, weak GPS, underground, phone dead, contradiction, no-show) with camera signals | Tests |
| Dashboard (`dashboard/`) | Design tokens (light/dark), ECharts; pages: Live lots (+ forecast vs actual), Detection log, Booking patterns (filters, sort, export), User drill-down (heatmap, timeline) | Builds |
| Mobile (`apps/mobile/`) | Expo Router: Overview, Scan (camera QR + manual), Alerts, History, Driver, Settings; mocks fall back only when an endpoint returns 404/405/501 | Typecheck + Android export; **never run on a device** |

Checks at this point: `uv run pytest` → **54 passed**; `npm --prefix dashboard run build` ok; `apps/mobile` `npx tsc --noEmit` ok.

## 3. Known weaknesses (be honest in the demo)
- **Rhythm exactness:** the engine predicts the next booking better than B2, but recovers exact day sets worse for several personas, e.g.:
  - routine switcher: 0.59 vs 0.99;
  - new user: 0.0 vs 0.9.

  Cause: no drift stage yet, and the habit test is too strict for short histories. **Phase 2 must fix this.**
- **Not built yet:** calibration, reminders and tiers are still simple (no calibration stage). Also missing: the simulator (`tools/simulator/`), WebSocket `/live`, driver confirm, rewards ledger.
- **Mobile:** still uses its own `src/pending-contract.ts` types for endpoints that now exist. Switch to the real `contract.ts` types; e.g. the booking-QR response is `{qr}` only.
- **Detection log (dashboard):** may still use mocked evidence. Point it at the real `GET /stays/{id}/evidence`.
- **Old branches:** `claude/feat-ops-p1-0776d5` is a duplicate ops implementation, superseded by awazzan2's work on `main`. Keep it for reference only and **don't merge it**. `origin/feat/ops-p1-continuation` is already in `main`.

## 4. Next: phase 2 (in priority order)
1. **Rhythm:**
   - `drift` stage (emerging/active/fading/ended + change date);
   - relax the habit test for new users;
   - `calibrate` (isotonic on validation weeks);
   - `remind` (τ from precision target, tiers, timing from `created_at`);
   - template reasons.

   Target: beat B2 on Hit@1 **and** day-set recovery. Brief: `docs/briefs/P3-rhythm.md` §P2.
2. **Simulator** `tools/simulator/`: replay `data/traces/*.jsonl` into `POST /signals` on the demo clock, then tune `config/fusion.yaml`. Brief: P2 §P2.
3. **Camera edge** `edge/camera/`: plate reader + spot watcher posting `camera_plate` / `camera_spot`. Brief: `docs/briefs/H-hardware.md`.
4. **Mobile:**
   - switch to the real contract types;
   - offline cache + outbox (`expo-sqlite`);
   - offline QR verification with `/keys/qr`.

   Brief: P1 §P2.
5. **Dashboard:** real evidence on the Detection log; evaluation page from `metrics.json`. Brief: P4 §P2–P3.
6. **Then phase 3:** driver confirm + rewards, `tools/demo.py` one-command demo, WebSocket `/live`.

Parallel sessions: give each one its brief + the kickoff prompt in `docs/briefs/README.md`. Run them in separate worktrees; ports 8001–8004 / 5174 to avoid clashes.

## 5. Open decisions (ask the user)
- iPhone driver build: needs a Mac or Apple developer account; otherwise the driver demo is Android-only.
- Presentation length / whether a live demo is required.
- What GoPark's lots really look like (own vs partner lots, gates, payment at exit). This affects whether cameras can be installed everywhere.

## 6. How to resume (any tool)
```bash
git clone https://github.com/adamk-codes/GoPark.git
```
```bash
uv sync
```
```bash
uv run python -m tools.generator --users 300 --weeks 26 --seed 42
```
```bash
uv run python -m rhythm run
```
```bash
uv run python -m rhythm evaluate
```
```bash
uv run uvicorn api.main:app --reload --port 8000
```
```bash
npm --prefix dashboard install
```
```bash
npm --prefix dashboard run dev
```
Mobile: `npm --prefix apps/mobile install`, put `EXPO_PUBLIC_API_URL=http://<laptop-LAN-IP>:8000` in `apps/mobile/.env`, then `npm --prefix apps/mobile run start` and scan with Expo Go.

## 7. Housekeeping rule
Before any session runs out of context it must update this file (done / in progress / next, with exact commands) and commit + push it. Never push code that breaks `main`; describe unfinished work here instead.
