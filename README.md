# GoPark Sense

GoPark × AUB hackathon. The focus is **Challenge 1: automatic arrival and departure detection, plus an attendant app**. Challenge 2 ("Rhythm", the booking-pattern engine) supports it by predicting who is about to arrive.

## ML at a glance

| Component | What it does | Methods | Code |
|---|---|---|---|
| **Plate reader** | Reads Lebanese plates at the gate, Latin and Arabic-Indic digits | YOLOv9-t plate detector (ONNX) + fast-plate-ocr, with EasyOCR for Arabic-Indic digits; optional YOLO11n vehicle boxes to cut false reads; multi-frame voting on OCR confidence | [`edge/camera/plate_reader.py`](edge/camera/plate_reader.py) |
| **Car-look match** | Suggests which expected booking a car is when the plate is unreadable | CLIP zero-shot colour/body classification, ranked against expected bookings, with a safety cap | [`edge/camera/vehicle_matcher.py`](edge/camera/vehicle_matcher.py) |
| **Spot watcher** | Detects when a single spot becomes occupied or free | YOLO11n box coverage of each spot polygon | [`edge/camera/spot_watcher.py`](edge/camera/spot_watcher.py) |
| **Rhythm** | Learns each driver's routine and predicts the next booking | Routine discovery, Bayesian habit test, CUSUM drift detection, isotonic calibration; backtested against 3 baselines with ablations | [`rhythm/`](rhythm/) |
| **Evidence fusion** | Decides check-in/out from camera, sensor, GPS and Rhythm signals, with explainable evidence | Weighted evidence fusion with confidence thresholds, tuned by replaying simulated traces | [`api/ops/`](api/ops/) |

**Results** (synthetic data; real-world accuracy not yet measured, see [Limitations](HANDOFF.md#5-limitations)):

| Measure | Result |
|---|---|
| Rhythm, next-booking Hit@1 on test weeks | **0.718** vs 0.684 for the best baseline |
| Rhythm, weekly day-set recovery (Jaccard) | **0.976** vs 0.874 |
| Detection, 674 simulated bookings replayed through the API | 93% confirmed automatically, 97% with driver confirmation, 0 false confirmations |
| Plate OCR, Western-digit plates (clean, blur, angle, dark, noise) | 5/5 crops, 5/5 whole frames; ~100 ms/frame on CPU |
| Plate OCR, Arabic-Indic crops (EasyOCR) | 19/30 (63%), preliminary |

**Reproduce:**

```bash
uv sync && uv run pytest                                          # 90+ tests
uv run python -m rhythm evaluate                                  # backtest vs baselines → runs/<run_id>/metrics.json
uv sync --group edge && uv run --group edge python -m edge.tests.eval_ocr --ocr fast auto easyocr
```

**Authors:** Adam Al Khatib (system design, plate reader and gate pipeline, Rhythm, evidence fusion, simulator, docs) with a teammate who built the car-look (CLIP) match.

Read these first:
- [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) — what we're building and why.
- [CONTRACT.md](CONTRACT.md) — the shared data formats and API every part codes against.

## Challenge 1: how detection works

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

Challenge 2 (Rhythm, the booking-pattern engine) learns each driver's routines and feeds these predictions; see [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md).

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

## Integrating a presence sensor (API reference)
The API also accepts presence-sensor events. A gate lane has a pair of presence sensors (IR break-beam or ultrasonic, ~1 m apart, on an ESP32). The order in which the two beams break gives the direction (A then B = `enter`, B then A = `exit`). The sensor says *a car passed*; the lane's plate camera says *who*. The full rules are in [CONTRACT.md](CONTRACT.md) §6 ("Gate presence sensor").

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

## Project structure
| Folder | Contents |
|---|---|
| `api/` | FastAPI backend: ops (scan, stays, evidence fusion, alerts, sync, driver confirm, rewards) and the Rhythm API |
| `rhythm/` | Booking-pattern engine: discovery, habits, drift, calibration, reminders, evaluation |
| `edge/` | Phone-camera software: plate reader, car-look match, spot watcher, gate-sensor simulator |
| `dashboard/` | Operator dashboard (React + Vite) |
| `apps/mobile/` | Attendant and driver app (Expo) |
| `tools/` | Data generator, signal simulator, one-command demo |
| `contract/`, `CONTRACT.md` | Shared schemas and types |
| `config/` | Lots, fusion weights, rewards, Rhythm settings |
| `docs/` | Detection design, demo script, task briefs |

Project status, results and roadmap: [HANDOFF.md](HANDOFF.md).
