# GoPark Sense: project status and handoff

Repository: https://github.com/adamk-codes/GoPark (default branch `main`) · Last updated: 2026-11-01

## 1. Overview
GoPark Sense addresses both GoPark × AUB hackathon challenges as one system:
- **Challenge 1: automatic arrival and departure detection, plus an attendant app.**
  - Gated lots: a gate camera with an ML model (plate + car look). Single spots: a presence sensor per spot. Ungated lots: the valet/attendant handles check-in and check-out in the app.
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

What we install depends on the kind of lot:

| Lot type | What is installed | How check-in / check-out works |
|---|---|---|
| **Gated lot** | **A camera at the gate**, with our ML model | The camera reads the plate on entry and exit. If the plate can't be read, the car-look model (colour, body, make) suggests which expected booking it is. The confidence is high → automatic check-in/out. Uncertain → a "Is this you? Yes / Not me" message to the driver. |
| **Single spots** (individual bookable spaces) | **A presence sensor per spot**, no camera | The sensor sees the spot become occupied or free. The booking for that spot at that time, plus the driver's phone GPS, says who it is. A confirmation message settles any doubt. |
| **Ungated lot** | **Nothing** | No automatic detection. The valet/attendant checks cars in and out from the attendant app (scan the booking QR, booking ID or plate) and fixes anything wrong. |

The same rules apply everywhere:
- **Phone GPS is only an early warning** ("arriving soon"). It never checks anyone in alone.
- **Rhythm** (Challenge 2) predicts who is expected, which raises the starting confidence and fills the attendant's "expected soon" list.
- **The valet/attendant can always override** from the app: check in, check out, walk-ins, resolve alerts. The app works offline.
- **Every decision is explainable** in the dashboard's Detection log.

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
| Field test of the gate camera on real plates | In progress |
| Spot presence sensors for single spots | In progress |
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
