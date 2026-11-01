# Brief C1: detection, the current priority

**Scope:** lots with presence sensors + a plate camera at the entry and exit, plus phone GPS. The final check
is a **confirmation message** to the driver. **No NFC, and no lots without an entry/exit camera** (owner
decision, 2026-11-01). Don't build anything for those.

## Who does what (assigned 2026-11-01)
| Who | Tasks, in order | Notes |
|---|---|---|
| **awazzan2** (GitHub) | **T1 → T3 → T4** | Code, starting now. The owner's laptop is off, so no local Claude sessions are running. Push a branch per task and open a PR; get `uv run pytest` green before merging. |
| **Cursor engineer** (gate sensors) | **T5** | Follow `edge/INTEGRATION.md` and the README section "Integrating a gate sensor". Test with `edge/sensor_sim.py` first. |
| **Adam** (owner) | **T2, T6** | Phones and real cars: plate field test, then the mobile app on an Android phone. |

**Kickoff prompt for awazzan2's Claude Code / Cursor** (paste it as is):
> Read HANDOFF.md, docs/DETECTION.md and docs/briefs/C1-detection.md. Do task T1 (car-look match) on branch `feat/c1-t1-car-look`, following the brief: contract first, tests, honest accuracy numbers. Then do T3 (parking confirmation message) and T4 (check-out message + bill) the same way, one branch each. No NFC and no lots without cameras: they are out of scope. Before you run out of context, update HANDOFF.md (done / in progress / next) and push.

Read first:
- `HANDOFF.md`
- `docs/DETECTION.md` (the technique chain and the message table this brief builds out)
- `CONTRACT.md` §6–§7
- the shared rules in `docs/briefs/README.md`

**Contract first:** any new shape goes into `CONTRACT.md` + `contract/schemas/` + `contract/types.ts`, then
`npm run contract` in `dashboard/` and `apps/mobile/`.

Done means:
- tests pass: `uv run pytest`;
- the dashboard builds;
- the mobile `npx tsc --noEmit` passes;
- `HANDOFF.md` is updated.

Don't break `main`.

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
  - **Capped so it can never reach the auto threshold on its own.** The best it can do is the confirmation-message tier (T3), plus an attendant suggestion ("Probably Rania's blue BMW?").
  - Add the weights to `config/fusion.yaml`.
- **Evaluation:**
  - Build a small labelled test set (public car images by colour and type, e.g. a Stanford Cars subset or your own photos; keep images out of git if their licence requires it).
  - Report top-1 colour, top-1 body and match accuracy vs the number of expected cars (1, 3, 5, 10).
  - Put the honest numbers in `edge/README.md`.
- **Tests:** unit tests with a stubbed model, plus the fusion cap test (vehicle-only evidence never auto-confirms).

## T3: Parking confirmation message (the final check)
The table of messages and answers is in `docs/DETECTION.md` → "The confirmation message". The driver-confirm API
already exists (`POST /stays/{id}/confirm`, `GET /users/{id}/pending_confirmations`), so this task extends it.
- **Server:**
  - Create a **notification** for the booking's user on every check-in:
    - auto: "Checked in … Not you?";
    - uncertain (0.60–0.95): "Did you just park …? Yes / Not me", with the car look and the time.
  - Contract: `GET /users/{id}/notifications?after_id=` → `[{id, stay_id, kind: "checked_in"|"confirm_arrival"|"checked_out"|"overstay", title, body, actions:["yes","not_me"]|["wrong"]|[], created_at, answered}]`. Also push it over `WS /live`.
  - Answers go through the existing confirm endpoint.
  - **A "Yes" counts as strong evidence only if the phone is at the lot:** add an optional `location {lat, lon, accuracy_m}` to `ConfirmRequest`, compare it with the lot geofence, and otherwise use a weak log-LR.
  - "Not me" on an auto check-in → a dispute alert for the attendant.
  - An uncertain stay with no answer after 10 min → "Needs eyes".
- **Mobile (driver):**
  - An in-app notification list + banner, a local notification (`expo-notifications`) when a new one arrives over polling or WS, and Yes / Not me buttons sent through the outbox, with the location attached.
  - Remote push needs an Expo development build (not Expo Go on Android). For the demo, use local notifications triggered by the app; note the dev-build path in HANDOFF.
- **Simulator:** count how many uncertain stays the messages resolve and the time to resolve. Add it to `runs/fusion_metrics.json`.
- **Tests:** a "Yes" far from the lot doesn't confirm; "Not me" raises a dispute; no answer goes to the attendant.

## T4: Check-out message + bill
- `GET /stays/{id}/bill` → `{billed_from, billed_to, minutes, amount_usd, rule}`, from sensor/camera times.
  - Rules: `exact` (evidence, driver or attendant check-out) and `booking_end` (inferred check-out bills to the later of booking end and the inferred time).
  - Rates come from `config/lots.yaml` / `fusion.yaml` `overstay_usd_per_15_min`.
- A notification on check-out ("You left BDD at 10:42. 2 h 30 min, $X") with a "That's wrong" action → attendant alert. An overstay notification every 15 min.
- Show the bill on the stay in the attendant app and on the dashboard Detection log.

## T2: Field test the plate camera on real plates (needs a human with phones)
- Point an old phone (IP Webcam) at a lane or driveway. Record 20–50 real Lebanese cars in day, dusk and night (`edge/README.md` has the setup).
- Run `uv run --group edge python -m edge.tests.eval_ocr` on the clips (extend it to take recorded clips). Report:
  - read rate;
  - exact-match rate;
  - one-character-off rate;
  - read rate with Arabic digits.
- Put the results in `edge/README.md` and set `config/fusion.yaml` camera weights from them. **This replaces the synthetic numbers in the pitch.**

## T5: End-to-end gate test with the real sensor (with the Cursor engineer)
- Bench setup: the ESP32 gate sensors post `gate_sensor` (see `edge/INTEGRATION.md`), and a phone camera runs `plate_reader --trigger api`.
- The API and dashboard Detection log run on the laptop.
- Script 10 passes: booked, walk-in, unreadable plate (cover it → T1 suggestion → T3 message), exit, and re-entry. Record the results in `docs/FIELD_TEST.md`.
- Add the run to `docs/DEMO.md` as the live hardware segment.

## T6: Mobile on a real Android phone (needs a human)
- Use Expo Go: follow HANDOFF.md §6.
- Walk through scan QR, walk-in, offline mode (airplane mode, then sync), alerts, driver confirm and rewards. Fix what breaks.
- Note in HANDOFF which features need a development build (remote push, background geofence).
