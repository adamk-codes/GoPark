# GoPark Sense: project status and handoff

Repository: https://github.com/adamk-codes/GoPark (default branch `main`) · Last updated: 2026-11-01

## 1. Overview
GoPark Sense addresses both GoPark × AUB hackathon challenges as one system:
- **Challenge 1: automatic arrival and departure detection, plus an attendant app.**
  - Gate presence sensors and a phone-based plate camera detect every car entering and leaving.
  - An evidence-fusion engine decides check-in and check-out.
  - Uncertain cases are confirmed by a short message to the driver, or by the attendant.
- **Challenge 2: Rhythm, the booking-pattern engine.**
  - It learns each driver's routines from booking history and predicts upcoming arrivals.
  - Those predictions set the starting confidence for detection and fill the attendant's "expected soon" list.

Key documents:

| Document | Contents |
|---|---|
| `ARCHITECTURE_PLAN.md` | System design |
| `CONTRACT.md` | All shared data shapes and API endpoints |
| `docs/DETECTION.md` | Detection techniques and driver messages |
| `docs/DEMO.md` | Five-minute demo script |
| `README.md` | Setup and run commands |

## 2. Detection design (Challenge 1)

| # | Technique | Role |
|---|---|---|
| 1 | Booking + Rhythm prediction | Who is expected, where and when; the starting confidence |
| 2 | Phone GPS + motion | Early "arriving soon" warning and supporting evidence |
| 3 | Gate presence sensors (2 per lane, ESP32) | Every car in or out at the exact second, with direction |
| 4 | Plate camera (Android phone at the gate) | Identifies the car; paired with the sensor event within ±8 s |
| 5 | Car-look match (CLIP zero-shot: colour, body, make) | Suggests the booking when the plate is unreadable; never confirms alone |
| 6 | Driver confirmation message ("Is this you? Yes / Not me") | Settles uncertain cases; a "Yes" counts only if the phone is at the lot |
| 7 | Attendant app (QR / booking ID / plate scan, works offline) | Final fallback, walk-ins and disputes |

Fusion combines all the evidence into one confidence score:

| Confidence | Action |
|---|---|
| ≥ 0.95 | Automatic check-in or check-out |
| 0.60–0.95 | Driver confirmation |
| < 0.60 | Attendant review |

The scope is lots with a camera and sensors at the entry and exit.

## 3. What is built (on `main`)

| Area | Features |
|---|---|
| Ops API (`api/ops/`) | Scan by signed QR / booking ID / plate; walk-ins; check-out; evidence fusion with saved, explainable evidence; gate-sensor + plate pairing; unidentified-car handling; departures and inferred check-outs; overstay, no-show, low-confidence and anomaly alerts; driver confirm; rewards ledger; offline sync with cursor; `WS /live`; `GET /signals`; demo clock |
| Camera edge (`edge/`) | Plate reader (Latin and Arabic-Indic digits, sensor-triggered mode, one camera for both directions); car-look match (CLIP); spot watcher; sensor simulator; integration guide (`edge/INTEGRATION.md`) |
| Rhythm (`rhythm/`) | Routine discovery, habit test, drift detection (CUSUM), isotonic calibration, reminder tiers, plain-language explanations, evaluation against baselines |
| Simulator (`tools/simulator/`) | Replays driver traces through the API on the demo clock; evaluates and tunes fusion |
| Dashboard (`dashboard/`) | Live lots with forecast vs actual, Detection log (evidence per decision), alerts, booking patterns, driver drill-down, evaluation page, demo banner |
| Mobile (`apps/mobile/`, Expo) | Attendant: overview, scan, walk-ins, alerts, history, offline cache and outbox, offline QR verification. Driver: confirmation cards, rewards, optional geofence |
| Demo (`tools/demo.py`) | One command starts everything and replays a narrated morning |

Verification: `uv run pytest` passes (90+ tests), the dashboard builds, and the mobile typecheck passes.

## 4. Results (synthetic data)

| Measure | Result |
|---|---|
| Detection, 674 simulated bookings, replayed through the API | 93% confirmed automatically, 97% with driver confirmation, 0 false confirmations |
| Rhythm, next-booking accuracy (Hit@1, test weeks) | 0.718 vs 0.684 for the best baseline |
| Rhythm, weekly day-set match (Jaccard) | 0.976 vs 0.874 |

## 5. Limitations
- All results come from generated data. The plate reader and the car-look match have not yet been measured on real Lebanese plates and cars.
- The fusion weights were tuned on synthetic data and should be re-tuned on field data.
- Cars with a dead phone and an unreadable plate are the hardest case and rely on the car-look suggestion or the attendant.
- The mobile app has been verified by typecheck and Android export only. Remote push notifications and background location need an Expo development build.

## 6. Roadmap

| Item | Status |
|---|---|
| Car-look match when the plate is unreadable | Done |
| Driver confirmation message on check-in | In progress |
| Check-out message and bill | Planned |
| End-to-end test with the gate sensors and phone camera; field test on real plates | In progress |
| Mobile app tested on Android devices | Planned |

Task details: `docs/briefs/C1-detection.md` and `docs/briefs/CURSOR-phone-camera.md`.

## 7. How to run
```bash
git clone https://github.com/adamk-codes/GoPark.git
```
```bash
uv sync
```
```bash
uv run python -m tools.demo
```
This one command generates the data, runs Rhythm, starts the API (:8000) and the dashboard (:5173), and replays the demo morning.

Manual steps:
```bash
uv run python -m tools.generator --users 300 --weeks 26 --seed 42
```
```bash
uv run python -m rhythm run
```
```bash
uv run uvicorn api.main:app --port 8000
```
```bash
npm --prefix dashboard install
```
```bash
npm --prefix dashboard run dev
```

Camera: `uv sync --group edge`, then see `edge/README.md`.

Mobile: `npm --prefix apps/mobile install`, set `EXPO_PUBLIC_API_URL=http://<laptop-LAN-IP>:8000` in `apps/mobile/.env`, then `npm --prefix apps/mobile run start`.
