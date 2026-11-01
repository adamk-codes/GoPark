# Brief C1: detection, the current priority

Read first:
- `HANDOFF.md`
- `docs/DETECTION.md` (the technique chain this brief builds out)
- `CONTRACT.md` §6–§7
- the shared rules in `docs/briefs/README.md`

**Contract first:** any new shape goes into `CONTRACT.md` + `contract/schemas/` + `contract/types.ts`, then
`npm run contract` in `dashboard/` and `apps/mobile/`.

Each task below is self-contained and can be picked up by any Claude Code, Cursor or cloud session.
Work on a branch `feat/c1-<task>`. Done means:
- tests pass: `uv run pytest`;
- the dashboard builds;
- the mobile `npx tsc --noEmit` passes;
- `HANDOFF.md` is updated.

Don't break `main`.

Ports, if you run in parallel:

| Port | Task |
|---|---|
| 8001 | T1 |
| 8002 | T2 |
| 8003 | T3 |
| 8004 | T4 |
| 8005 | T5 (the edge default) |

## T1: Car-look match when the plate is unreadable (ML, highest priority)
Goal: when a sensor event has no plate read within the pairing window, suggest which expected booking the car is.
- **Data:**
  - Add a vehicle profile `{color, body, make, model?}` to users (CONTRACT + generator).
    - Colors: white/silver/black/grey/blue/red/green/beige.
    - Bodies: sedan/hatchback/suv/pickup/van.
  - Let the generator pick realistic Lebanese frequencies, with lots of white, silver and black, and common Kia/Toyota/Hyundai/Nissan/Mercedes/BMW.
  - Add an endpoint the edge can call: `GET /lots/{id}/expected_vehicles?around=<ts>&window_min=20` → `[{booking_id, user_id, vehicle, p}]`.
- **Edge:** `edge/camera/vehicle_matcher.py`.
  - Use the car crop the plate reader already has (YOLO `--cars`) and a zero-shot model from Hugging Face. Default: `openai/clip-vit-base-patch32`; allow a smaller one via a flag. Keep it in the optional `edge` dependency group.
  - Score the crop against text prompts built from each expected vehicle ("a photo of a blue BMW SUV").
  - Post a new signal `camera_vehicle` with `payload {candidates:[{booking_id, score}], top_margin}`.
  - Privacy: process frames in memory only, as the plate reader does.
- **Fusion:**
  - Its log-LR depends on how *unique* the match is: a big margin over the 2nd candidate, and how many expected cars share the colour and body.
  - **Capped so it can never reach the auto threshold on its own.** The best it can do is the ask-driver tier, plus an attendant suggestion ("Probably Rania's blue BMW?").
  - Add the weights to `config/fusion.yaml`.
- **Evaluation:**
  - Build a small labelled test set (public car images by colour and type, e.g. a Stanford Cars subset or your own photos; keep images out of git if their licence requires it).
  - Report top-1 colour, top-1 body and match accuracy vs the number of expected cars (1, 3, 5, 10).
  - Put the honest numbers in `edge/README.md`.
- **Tests:** unit tests with a stubbed model, plus the fusion cap test (vehicle-only evidence never auto-confirms).

## T2: Field test the plate camera on real plates (needs a human with phones)
- Point an old phone (IP Webcam) at a lane or driveway. Record 20–50 real Lebanese cars in day, dusk and night (`edge/README.md` has the setup).
- Run `uv run --group edge python -m edge.tests.eval_ocr` (extend it to take recorded clips) on the clips. Report:
  - read rate;
  - exact-match rate;
  - one-character-off rate;
  - read rate with Arabic digits.
- Put the results in `edge/README.md` and set `config/fusion.yaml` camera weights from them. **This replaces the synthetic numbers in the pitch.**

## T3: Secure NFC sticker
- **Tags:** NTAG 424 DNA with SUN (Secure Unique NFC). Each tap gives a URL with `picc_data` (encrypted UID + counter) and `cmac`.
- **Server:**
  - Verify `cmac` with the per-tag key, and reject replays (the counter must increase).
  - Keep tag keys in `keys/nfc/` (gitignored) and the tag→lot map in `config/nfc_tags.yaml`.
  - Endpoint: `POST /nfc/tap {url | picc_data+cmac, device_id, user_id, observed_at, location?}` → an `nfc` signal for the user's booking at that lot (enter or exit from the stay state).
- **Phone:**
  - Prefer **URL-in-tag → deep link into the app** (Android and iPhone both read NDEF URLs in the background).
  - Fallback: read in the app with `react-native-nfc-manager`. That needs an Expo **development build** (it won't work in Expo Go), so note it in `apps/mobile/AGENTS.md` / HANDOFF.
- Add a tag simulator for tests (generate valid SUN URLs with a test key).

## T4: Valet "tap to call your car" + stop-the-clock billing + tap bonuses
- **Valet:**
  - `POST /stays/{id}/request_car {device_id, client_event_id, via: "nfc"|"app"}` → an alert `car_requested` for the valet attendant app, with plate, car look and spot.
  - The attendant marks it handed over, which checks the stay out (method `attendant`).
- **Billing:**
  - `GET /stays/{id}/bill` → `{billed_from, billed_to, minutes, amount_usd, rule}`. Rules:
    - `exact`: auto, driver, attendant or NFC check-out;
    - `booking_end`: an inferred check-out bills to the later of booking end and the inferred time.
  - Show it on the stay in the attendant app and on the dashboard.
- **Rewards:** `config/rewards.yaml` adds `nfc_tap` and `tap_out` points.
- **Mobile:** a valet queue on the attendant app ("Bring car: B123456 blue BMW, spot 14") and a "Call my car" button for the driver.

## T5: End-to-end gate test with the real sensor (with the Cursor engineer)
- Bench setup: the ESP32 gate sensors post `gate_sensor` (see `edge/INTEGRATION.md`), and a phone camera runs `plate_reader --trigger api`.
- The API and dashboard Detection log run on the laptop.
- Script 10 passes: booked, walk-in, unreadable plate (cover it → T1 suggestion), exit, and re-entry. Record the results in `docs/FIELD_TEST.md`.
- Add the run to `docs/DEMO.md` as the live hardware segment.

## T6: Mobile on a real Android phone (needs a human)
- Use Expo Go: follow HANDOFF.md §6.
- Walk through scan QR, walk-in, offline mode (airplane mode, then sync), alerts, driver confirm and rewards. Fix what breaks.
- Note in HANDOFF which features need a development build (NFC, background geofence).
