# Gate sensor → GoPark API: integration guide

For whoever builds the gate presence sensor: two beams or ultrasonic sensors about 1 m apart on an ESP32,
one pair per lane. The sensor says *a car moved, in this direction*. The lane camera
(`edge/camera/plate_reader.py`) says *who*. The API pairs the two (CONTRACT.md §6, "Gate presence
sensor"). This page is everything the sensor must do to fit in.

## 1. Endpoint

```
POST http://<laptop-ip>:<port>/signals
Content-Type: application/json
```

The body is one signal, or `{"signals": [ … ]}` for a batch (use batches when emptying the offline queue).
The response is `200 {"accepted": n, "duplicates": m}`. A `422` means the body is invalid; it is not
retryable, so log it and drop it. The demo laptop runs the API on port 8000; this workstream's test
instance runs on 8005.

## 2. The JSON (exactly this shape)

```json
{
  "client_event_id": "6f0d8a4e-3c1b-4f7e-9a59-2f1e0c9b7d11",
  "device_id": "gate-AUB-1",
  "lot_id": "AUB",
  "source": "gate_sensor",
  "kind": "enter",
  "observed_at": "2026-11-16T08:01:10+02:00",
  "payload": {"gate_id": "AUB-1", "lane": 1}
}
```

| Field | Rule |
|---|---|
| `client_event_id` | A new random UUID v4 per car passage. Generate it **once**, when the event happens, and reuse it on every retry. That is what makes retries safe (see §4). |
| `device_id` | `gate-<LOT>-<n>`, one per ESP32. |
| `lot_id` | Lot id from `config/lots.yaml`: `AUB`, `BDD`, `LAU`, `HAMRA`, `VERDUN`, `ACHRAFIEH`. |
| `source` | Always `"gate_sensor"`. |
| `kind` | `"enter"` when sensor A (street side) triggers before B (lot side), `"exit"` when B triggers before A. Never send anything else: a car that backs out (A, B, A) or a person crossing one beam is no event. |
| `observed_at` | When the car crossed. Use the moment the **second** sensor fired, not the moment you post. ISO 8601 with a UTC offset, seconds precision. See §3. |
| `payload.gate_id` | `<LOT>-<n>`, the same `n` as in `device_id`. |
| `payload.lane` | Integer lane number at that gate (1 if there is one lane). |

Do not send `user_id`, `booking_id`, a plate, or any other top-level field. The contract schema
(`contract/schemas/signal.schema.json`) has `additionalProperties: false`. Extra keys inside `payload`
are allowed but ignored.

## 3. Clock

The API pairs a sensor event with a plate read when both have the same lot and direction and their
`observed_at` values are within **±8 s** (`config/fusion.yaml` → `gate_pairing.window_s`). So the sensor
and the camera must agree on the time to within a couple of seconds.

- **Demo (default):** the API runs a demo clock that can be fast-forwarded. Read
  `GET /dev/clock` → `{"now": "2026-11-16T07:50:00+02:00"}`. Keep the offset between that and the
  ESP32's `millis()`, and re-read it every 60 s (someone may move the clock). The camera scripts do the
  same by default (`--clock demo`).
- **Field test:** real time from NTP (`configTime` on the ESP32), sent with the Beirut offset: `+02:00` in
  winter, `+03:00` in summer. In POSIX TZ form that is `EET-2EEST,M3.5.0/0,M10.5.0/0`. Run the camera
  scripts with `--clock field`.

If the time is wrong by more than 8 s, nothing pairs. Every car then shows up as "identity unknown"
(see §6).

## 4. Offline queue and retry

Wi-Fi at a gate drops. Never lose an event, and never send one twice as two different events.

1. On each passage, build the JSON (UUID and `observed_at` fixed now) and push it onto a queue in RAM.
   Keep at least 50 entries; drop the oldest when full.
2. Post the queue head (or the whole queue as `{"signals": [...]}`). On `2xx`, remove those entries.
   `duplicates > 0` is fine: it means an earlier attempt already arrived.
3. On a network error, timeout or `5xx`: keep the entries and retry with backoff (1, 2, 5, 10, 10 … s).
   Keep the original `client_event_id` and `observed_at`.
4. On `422`: drop that entry and log it. It will never be accepted.

The API stores each `client_event_id` once (`INSERT OR IGNORE`), so resending after a lost response is
harmless. Late events still pair if the plate read arrives too ("resolved later" in CONTRACT §6).

## 5. Wake the plate reader

By default the camera reads all the time and posts plates on its own. For one camera per gate,
especially one camera serving both directions, run it in triggered mode. It then posts only the best
plate read within ±8 s of each sensor event, with the sensor's direction and `gate_id` / `lane`.

**Preferred.** The reader polls `GET /signals?source=gate_sensor&lot_id=&after_id=` (CONTRACT §7; that
endpoint exists). One command reads the lane from `config/gates.yaml` (phone URL, ROI, fps, confidence):

```bash
uv run --group edge python -m edge.run_gate --lot-id AUB --phone http://PHONE_IP:8080
```

That starts `plate_reader` with `--kind auto --trigger api --cars --vehicle-match`. The same flags by hand:

```bash
uv run --group edge python -m edge.camera.plate_reader --source http://PHONE_IP:8080/video --lot-id AUB --device-id cam-gate-1 --kind auto --trigger api --cars --vehicle-match --api http://localhost:8000
```

The ESP32 only POSTs to the API. It does not need a second post. Ctrl+C stops `run_gate`, including on Windows.

**Fallback.** `--trigger listen`, and also POST the same JSON to `http://<laptop-ip>:8090/gate`:
fire-and-forget, timeout ≤ 1 s, no retry. The API post stays the one that counts. The reader stamps the
event when it *arrives*, so a few hundred ms of Wi-Fi delay does not matter.

```bash
uv run --group edge python -m edge.camera.plate_reader --source http://PHONE_IP:8080/video --lot-id AUB --device-id cam-gate-1 --kind auto --trigger listen --listen 0.0.0.0:8090
```

Without a trigger, use the default always-on mode (`--kind enter` / `exit`): the API still pairs on `observed_at`.

## 6. Check pairing

**Simulator to compare against:** `edge/sensor_sim.py` posts exactly what the sensor should post.

Type `e` (car in) or `x` (car out), then Enter, for each car:

```bash
uv run --group edge python -m edge.sensor_sim --lot-id AUB --api http://localhost:8005 --also http://localhost:8090/gate
```

Or post on a timer:

```bash
uv run --group edge python -m edge.sensor_sim --lot-id AUB --every 20 --pattern enter,exit --count 6 --dry-run
```

`--dry-run` prints the JSON; diff it against what your firmware sends.

**In the dashboard:** open *Detection log* (`#detection/<LOT>/<stay_id>`), pick the lot, then the stay.
A paired car's evidence timeline shows a `gate_sensor` row (`enter`, log-LR **2.0**, the pairing bonus)
next to the `camera_plate` row, and the stay goes `CHECKED_IN` with method `auto`.

The same from the command line:

```bash
curl http://localhost:8005/stays/S-B016534/evidence
```

**Unpaired:** a sensor event with no plate within 8 s becomes, after the window (scheduler every 30 s,
or `POST /dev/tick`):
- for an entry: a stay `G-<signal id>` and a `low_confidence` alert "Car in … identity unknown";
- for an exit: it closes the oldest booking-less stay, or raises an "identity unknown" alert.

Lots of these means the clock is off, the camera is missing plates, or `lot_id` / direction is wrong.

**Measured here** (API on 8005, demo clock, synthetic gate video, `sensor_sim` + `plate_reader --trigger listen --kind auto`):

| Step | Result |
|---|---|
| Enter for booking B016534 (plate S715592) | `gate_sensor` enter at 07:50:15 + `camera_plate` enter at 07:50:16 → stay `S-B016534` `CHECKED_IN`, `auto`, P = 0.993 |
| Same camera, 2 h later | `gate_sensor` exit + `camera_plate` exit at 09:50:51 → `CHECKED_OUT`, `auto` |

## 7. Wiring reminder (for the person mounting it)

- Sensor A goes on the street side, B on the lot side, about 1 m apart, at bumper height (40–60 cm), across the lane.
- The camera faces the same lane and should see the plate while the car is between the beams.
- Label each ESP32 with its `device_id` and `gate_id`.
- Check with `--dry-run` first, then one real car each way, then look at the Detection log.

## 8. Firmware

`edge/firmware/gate_sensor/gate_sensor.ino` is the ESP32 sketch (Arduino core). It follows §2–§4, and the
same rules as `edge/gate_logic.py` (the unit tests check that module):

- one UUID v4 per completed passage, reused on every retry;
- `observed_at` is the second beam, to the second, not the POST time;
- a 50-deep RAM queue, dropping the oldest when it is full;
- backoff 1, 2, 5, then 10 seconds; `422` drops that entry; `2xx` removes it (`duplicates` is fine);
- A then B is `enter`, B then A is `exit`; one beam, or A-B-A, is not an event.

`CLOCK_MODE 0` uses NTP (`EET-2EEST,M3.5.0/0,M10.5.0/0`) and the camera must use `--clock field`.
`CLOCK_MODE 1` reads `GET /dev/clock` every 60 s, matching the camera's default `--clock demo`.
Edit the defines at the top (Wi-Fi, API host, lot, pins), flash, and diff a passage against
`edge/sensor_sim.py --dry-run` before pointing it at the live API. On the serial monitor, `e` or `x`
then Enter posts one passage without the beams.
