# Brief: phone camera at the gate, end to end (for the Cursor engineer)

Repo: https://github.com/adamk-codes/GoPark (branch from `main`). Work on `feat/c1-t5-phone-gate`, open a PR,
and don't break `main`.

## What we agreed (the behaviour to deliver)
Each lot has a gate lane with **your two presence sensors** (direction from trigger order) and **an Android
phone as the camera** (IP Webcam app, streaming over Wi-Fi to the site laptop). The laptop runs the API and the
camera software. All of it is already in the repo; your job is to make it work for real with your sensors and a
real phone, and prove it.

1. **Car enters:**
   - the sensors fire → your ESP32 posts `gate_sensor` `enter`;
   - the phone camera reads the plate within ±8 s;
   - the API pairs them → the booking's stay is `CHECKED_IN`, method `auto`.
2. **Plate unreadable** (dirty, covered, night):
   - the camera falls back to the **car-look match** (colour, body, make via CLIP, already built by awazzan2: `edge/camera/vehicle_matcher.py`, flag `--vehicle-match`);
   - it suggests the most likely expected booking, but never auto-checks in;
   - the stay waits for the driver's confirmation message, and otherwise goes to the attendant ("Needs eyes").
3. **No plate and no match:** the car still counts in occupancy as "identity unknown" for the attendant.
4. **Car leaves:** the sensors fire `exit` + a plate read → `CHECKED_OUT` at that second (billing is exact).
5. **Phone GPS** is only the early warning ("arriving soon"). The camera and sensors decide.

**Out of scope:** NFC, and lots without a camera. Don't build them. The driver confirmation message (T3) is
awazzan2's task; you only need the stays to reach the right state.

## Read first
- `HANDOFF.md`
- `docs/DETECTION.md`
- `edge/INTEGRATION.md`: the exact JSON your sensor posts, the clock rules, the offline queue, and how to check pairing.
- `edge/README.md`: phone setup (IP Webcam 1280×720, mounted at plate height 40–80 cm, straight on, 2–4 m before the barrier), install and flags.
- `CONTRACT.md` §6

## Run it
```bash
uv sync --group edge
uv run --group edge python -m tools.generator --users 300 --weeks 26 --seed 42
uv run --group edge uvicorn api.main:app --host 0.0.0.0 --port 8000
uv run --group edge python -m edge.camera.plate_reader --source http://PHONE_IP:8080/video --lot-id AUB --device-id cam-gate-1 --kind auto --trigger api --cars --vehicle-match --api http://localhost:8000
npm --prefix dashboard install && npm --prefix dashboard run dev
```

- Always use `uv run --group edge`. A plain `uv run` uninstalls the edge packages.
- `--trigger api` reads your sensor events from `GET /signals`, which now exists. `--trigger listen` (you also POST to `:8090/gate`) still works as a fallback.
- Test without hardware first: `uv run --group edge python -m edge.sensor_sim --lot-id AUB --api http://localhost:8000`.
- Use `--clock demo` (the default) for the demo and `--clock field` with NTP on the ESP32 for real time. They must agree to within a couple of seconds, or nothing pairs.

## Tasks
1. **Hardware loop:**
   - Point your ESP32 at `POST /signals` exactly as `edge/INTEGRATION.md` §2–§4 says: UUID per passage, reused on retry; `observed_at` is when the second beam fired; an offline queue.
   - Confirm in the dashboard Detection log that sensor + plate rows pair (`gate_sensor` log-LR 2.0) and the stay goes `CHECKED_IN` / `auto`.
2. **Phone camera for real:**
   - Mount the phone and tune `--roi`, `--fps`, `--min-conf` and `--need` for your lane.
   - One phone serving both directions with `--kind auto` is the target; use two phones (`--kind enter` / `--kind exit`) only if one can't see both.
   - Keep the phone on a charger with the screen awake (README §"Point an old phone at the gate").
3. **Scripted test, 10+ passes**, using real cars or printed Lebanese plates on toy cars:
   - booked car in;
   - booked car out;
   - walk-in (no booking);
   - plate covered → car-look suggestion;
   - plate covered and no match → identity unknown;
   - re-entry;
   - two cars back to back;
   - Wi-Fi unplugged mid-pass (the queue resends, no duplicates);
   - night/dim light.

   Record each pass in **`docs/FIELD_TEST.md`**: expected vs actual state, time to `CHECKED_IN`, plate read correct (y/n). Add the plate read rate and the car-look hit rate.
4. **One-command gate launcher:** `uv run --group edge python -m edge.run_gate --lot-id AUB --phone http://PHONE_IP:8080` starts the plate reader with the agreed flags. Read defaults from a small `config/gates.yaml` (lot → phone URL, lane, ROI). Ctrl+C stops it cleanly on Windows.
5. **Fix stale docs:** `edge/INTEGRATION.md` §5 still says `GET /signals` doesn't exist. It does now (CONTRACT §7). Update it.
6. **Add a "Live hardware" segment to `docs/DEMO.md`:** what to set up, what to show, and a fallback (`edge/sensor_sim.py` + a recorded clip via `--source clip.mp4 --realtime`) if the hardware or Wi-Fi fails on stage.

## Done means
- `uv run pytest` passes (add tests for anything new, e.g. `edge/tests/test_run_gate.py` and the gates config).
- `docs/FIELD_TEST.md` has real numbers. Write the bad ones too; they make the pitch honest.
- A short HANDOFF.md update (done / in progress / next), then push your branch and open a PR.
