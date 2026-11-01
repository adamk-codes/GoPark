# Handoff — current status

> **Read this first** if you're a new Claude Code / Cursor session picking up the work.
> Last updated: 2026-11-01. **Phases 1 and 2 are merged and pushed on `main`.** Phase 3 is merged too. Checks: `uv run pytest` gives **90 passed**; the dashboard builds; the mobile `tsc` passes.
> - **Ops:**
>   - fusion P2 (camera attribution, departures, anomaly and low-confidence alerts);
>   - `tools/simulator` (replay + evaluate + tune), `WS /live`;
>   - gate-sensor pairing (±8 s), tuned `config/fusion.yaml`.
>   - Replay before tuning: 91% auto-confirm, 3% missed. Tuned on synthetic data, 94% auto-confirm and 0 false confirms (may be overfit).
> - **Rhythm:** drift (CUSUM), relaxed habit test, isotonic calibration, remind tiers, explanations, evaluate v2. Test weeks: Hit@1 **0.718** vs B2 0.684; day-set Jaccard **0.976** vs B2 0.874.
> - **Edge:** plate reader, spot watcher, gate-sensor-triggered plate reading, `edge/sensor_sim.py`, `edge/INTEGRATION.md`. `--trigger api` uses `GET /signals` (built).
> - **Dashboard:** Detection log on real evidence, resolvable alerts, `useLive`, Evaluation tab.
> - **Mobile:** SQLite cache + outbox, offline QR check, alerts resolve/"Needs eyes". Driver confirm cards, Rewards screen, optional background geofence (P3). Uses the real confirm/rewards contract. **Never run on a device.**
>
> **Phase 3 merged on `main`** (90 tests pass):
> - ops: `POST /stays/{id}/confirm` (arrived / not_me / left); `GET /users/{id}/pending_confirmations`; rewards ledger (`config/rewards.yaml`); `GET /signals` with an `after_id` cursor; simulator `--driver-confirm`; README "Integrating a gate sensor". Offline replay with 80% of drivers answering: 97% confirmed, 0 false confirms.
> - dashboard: `uv run python -m tools.demo` (one-command scripted demo with narration), a Demo feed banner, occupancy that includes unidentified cars, `docs/DEMO.md`.
>
> **Next = Challenge 1 detection** (owner priority, 2026-11-01). Tasks T1–T6 are in **`docs/briefs/C1-detection.md`**, each self-contained for any Claude Code / Cursor / cloud session. The full technique chain (why each method needs the next) is in **`docs/DETECTION.md`**.
> 1. **T1 car-look match** (ML): CLIP zero-shot colour/body/make vs expected bookings when the plate is unreadable. Suggestion only, never auto.
> 2. **T2 plate field test** on real Lebanese plates; replace the synthetic numbers.
> 3. **T3 secure NFC sticker** (NTAG 424 DNA SUN) for lots without a camera.
> 4. **T4 valet "call your car"**, stop-the-clock billing, tap bonuses.
> 5. **T5 end-to-end gate test** with the Cursor engineer's sensors.
> 6. **T6 mobile on a real Android phone.**
>
> Owner-approved direction: no camera → phone signals + secure NFC + driver tap; gate → sensors + plate camera (+ car-look). Slides come after C1.
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
- Built: fusion pairing (ops) and sensor-triggered plate reading (edge). The ESP32 firmware belongs to the Cursor engineer; see `edge/INTEGRATION.md`.

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
- **All detection numbers are synthetic** (generator traces). The plate OCR has never seen real Lebanese plates; T2 fixes this.
- **Fusion tuning may be overfit:** the motion weight doubled on synthetic data that has no false motion signals. Re-tune on field data.
- **Valet and dead-phone cases are weakest:** 37% and 21% missed without a camera or scan. T1, T3 and T4 address this.
- **Mobile has never run on a device**, and NFC and background geofence need an Expo development build (not Expo Go).
- **Old branches:** `claude/feat-ops-p1-0776d5` is a duplicate ops implementation. **Don't merge it.**

## 4. Next: Challenge 1 detection
See `docs/briefs/C1-detection.md` (T1–T6) and `docs/DETECTION.md`. The old phase 1–3 briefs in `docs/briefs/` are done and kept for reference.

Parallel sessions: give each one a task from the C1 brief plus the kickoff prompt in `docs/briefs/README.md`. Run them in separate worktrees on ports 8001–8005.

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
