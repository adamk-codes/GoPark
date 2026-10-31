# Brief H — Hardware + edge sensing

**Branch:** `feat/edge` · **You own:** `edge/` · **Read:** `docs/hardware-kit.html` (shopping list + hands-on jobs), `CONTRACT.md` §5–§6, `ARCHITECTURE_PLAN.md` §3.1–§3.3

This brief has two readers:
- **the hardware helper (a person):** physical setup, phones, field test;
- **an optional Claude Code session** on the helper's laptop, which writes the `edge/` code.

Everything edge-side talks to the backend through **one endpoint: `POST /signals`** (CONTRACT §6). The backend's fusion decides what the evidence means.

## Decision pending: camera-first or hybrid
| | Camera-first (recommended) | Hybrid kit |
|---|---|---|
| Main sensor | Old phones as entrance camera (plate) + overhead camera (spots), streamed to the laptop | BLE beacons + NFC tags + phone geofence/motion |
| Signals sent | `camera_plate` (`payload.plate_norm`), `camera_spot` (`payload.space_id`, occupied/vacant) | `ble`, `nfc`, plus app-side `geofence`/`motion` |
| Hardware cost | ~$15–40 (clamps, NFC tags, cables) | ~$60–120 (+ optional demo gate) |
| Setup by hand | Mount 2 phones, aim, light check | Beacon IDs, NFC writing, dev builds, signal walk test |

Build the edge code for camera-first first. Add BLE/NFC only if the team picks the hybrid kit.

## Phase P1 (hours 3–12): setup
**Person:**
- [ ] Charge everything. Install a webcam/IP-camera app on 2 old phones; mount one facing the entrance at plate height, one overhead covering a few spots (a toy model or a printed parking-lot mat is fine for the live demo).
- [ ] Sort out developer mode on the test phones and help P1 install the development build (needed for background location/BLE/NFC). Accept every permission. Turn off battery optimisation.
- [ ] Ask a real lot for permission to film for the field test.

**Claude session (`edge/camera/`):**
- [ ] Add an optional `edge` dependency group to `pyproject.toml` (OpenCV, a YOLO car detector, a plate OCR library). This is a shared file, so open the PR to P2. Keep it out of the main install.
- [ ] `edge/camera/plate_reader.py`:
  - read the phone stream (URL from a flag);
  - detect cars, crop plates, OCR;
  - normalise (uppercase, Arabic-Indic → Western digits, strip spaces/dashes; check `^[A-Z]\d{1,6}$`);
  - when the same plate is seen in ≥ 2 of the last 5 frames, POST a `camera_plate` signal with `kind: "enter"`, or `"exit"` for the exit camera;
  - never store images, only the plate text.
- [ ] `edge/camera/spot_watcher.py`: spot polygons from a small JSON file (click-to-define helper script); car-in-polygon over N frames → `camera_spot` `occupied` / `vacant` with `payload.space_id`.
- [ ] Both send `client_event_id` (UUID), `device_id` (`cam-entrance-1`, `cam-overhead-1`), `lot_id`, `observed_at`.

## Phase P2 (hours 12–24): gate screen + demo gate
- [ ] `edge/gate-qr/index.html`: full-screen page that polls `GET /dev/gate/{lot_id}` (P2 adds it) and shows the rotating `GPG1` QR every 30 s. Runs on the old tablet's browser.
- [ ] *(Hybrid kit or spare time)* `edge/demo-gate/`: ESP32 firmware (Arduino) that polls the API (or listens on `/live`) and lifts a servo barrier plus a green LED on an auto check-in at the demo lot; red LED + beep on a rejected scan. The person wires it (servo: brown = GND, red = 5V, orange = signal); Claude flashes it over USB on the laptop running Claude Code.
- [ ] *(Hybrid kit only)* `edge/beacons.yaml` with the UUID/major/minor/power per beacon (matching `config/lots.yaml`) and the NFC URLs; the person types them into the vendor app and NFC Tools.

## Phase P3–P4 (hours 24–34): field test
- [ ] `edge/fieldtest/PROTOCOL.md`: scripted runs per scenario (arrive, park, leave; clean / weak GPS / underground / phone dead), at least 5 runs each, with a person tapping a ground-truth button on a second phone at the real moment of each event (a tiny page that POSTs `source: "attendant"` signals with `payload.truth = true` is enough).
- [ ] **Person:** do the runs and film them (phone screen + car in shot).
- [ ] `edge/fieldtest/analyze.py`: joins logged signals and ground truth, computes:
  - auto-confirm rate;
  - false-confirm rate;
  - median time-to-confirm;
  - plate OCR accuracy per lighting condition.

  Writes `fieldtest_metrics.json` for P4's evaluation page and slides.

## What only the person can do
Buying, mounting and aiming hardware, phone permissions and developer mode, accounts and passwords, lot permission, wiring, the field-test drive and filming, holding props in the demo. Full list: `docs/hardware-kit.html`.

## Gotchas
- Plate OCR on Lebanese plates is unproven: report measured accuracy, never assume it. The attendant can always confirm the OCR text.
- Use the demo clock for `observed_at` in the live demo (`GET /dev/clock`). In the field test, use real time and analyse it separately.
- Privacy: process frames in memory, send only plate text, no video saved except the deliberate demo recording.
