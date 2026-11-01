# Camera edge (`edge/camera/`)

Old phones stream video to the laptop; two scripts turn that into signals for `POST /signals`
(CONTRACT §6). The backend's fusion decides what they mean.

| Script | Camera | Sends |
|---|---|---|
| `plate_reader.py` | entrance (or exit) phone at plate height | `camera_plate`, kind `enter` / `exit`, `payload.plate_norm` (+ `ocr_conf`) |
| `spot_watcher.py` | overhead phone over a few spots | `camera_spot`, kind `occupied` / `vacant`, `payload.space_id` (+ `coverage`) |
| `define_spots.py` | (helper) | click spot polygons on one frame → `spots.json` |

Every signal has a UUID `client_event_id`, a `device_id` (`cam-entrance-1`, `cam-overhead-1`), `lot_id` and
`observed_at`, and is checked against `contract/schemas/signal.schema.json` before it leaves.
**Privacy:** frames are processed in memory and dropped. No image or video is written; only plate text
and spot ids are sent.

## Install

```bash
uv sync --group edge
```

A plain `uv sync` does **not** install this group. Run every edge command with `uv run --group edge …`.
A plain `uv run` would sync back to the default groups and uninstall the edge packages.

Download sizes (CPU only, Windows, measured here):

| What | Size | When |
|---|---|---|
| Python packages (torch CPU, torchvision, ultralytics, opencv, onnxruntime, easyocr, fast-alpr) | ~250 MB download, ~1.3 GB in `.venv` | `uv sync --group edge` |
| Plate detector `yolo-v9-t-384-license-plates-end2end.onnx` | 7.4 MB | first run → `~/.cache/open-image-models` |
| Plate OCR `cct_xs_v2_global.onnx` | 3.2 MB | first run → `~/.cache/fast-plate-ocr` |
| Car detector `yolo11n.pt` | 5.4 MB | first `--cars` / `spot_watcher --detector yolo` run → `edge/models/` (gitignored) |
| EasyOCR Arabic + English (detector + recogniser) | 285 MB | first `--ocr auto|easyocr` run → `~/.EasyOCR` (load ≈ 15 s once cached) |

Fetch the models before the demo, on good Wi-Fi: run each script once with `--dry-run`.

`easyocr` pulls `opencv-python-headless` next to `opencv-python`. Both provide `cv2`; in this install the
GUI build wins, so `define_spots.py` and `--show` work. If windows ever fail with
"The function is not implemented", run `uv pip install --force-reinstall opencv-python`.

## Point an old phone at the gate

1. **App:** install *IP Webcam* (Android, free, by Pavel Khlebovich). On iPhone, use any app that serves
   an MJPEG or RTSP stream (e.g. *IP Camera Lite*). Put the phone on the same Wi-Fi as the laptop, or on
   the laptop's hotspot.
2. **Settings:** in IP Webcam, set video resolution to **1280×720** and quality to ~70. Turn off audio.
   Turn on "Prevent sleep" (or keep the screen on). Turn off battery optimisation for the app. Keep the
   phone on a charger. Tap **Start server**.
3. **URL:** the app shows `http://<PHONE_IP>:8080`. The stream is `http://<PHONE_IP>:8080/video` and a
   single frame is `http://<PHONE_IP>:8080/shot.jpg`. Open the stream in the laptop browser first to check.
4. **Angle (entrance):** mount at plate height (40–80 cm), **straight on**, at most ~20° to the side or
   up/down. Put it 2–4 m before the barrier, where cars stop or slow down. The plate should fill at least
   ~1/6 of the frame width when the car is at the barrier; at 720p that is ≥ 200 px. Use `--roi x,y,w,h`
   to crop to the lane if the frame also shows the street.
5. **Lighting:** avoid the sun or headlights straight into the lens (the plate washes out). At night you
   need a lamp on the plate; the phone's LED torch (IP Webcam → *Video preferences → Flashlight*) is
   enough at 2 m. A dirty lens costs more accuracy than a cheap phone.
6. **Overhead (spots):** as high as you can, looking down at the spots, so cars do not hide each other.
   For the table demo, a phone on a stand over a printed lot mat with toy cars is fine.

## Run

API for local tests (port 8005 for this workstream; the demo uses whatever port the API runs on):

```bash
uv run --group edge uvicorn api.main:app --port 8005
```

**Entrance camera** (demo clock, posts to the API):

```bash
uv run --group edge python -m edge.camera.plate_reader --source http://PHONE_IP:8080/video --lot-id AUB --device-id cam-entrance-1 --kind enter --api http://localhost:8005
```

Exit camera: the same with `--device-id cam-exit-1 --kind exit`. Useful flags:
- `--dry-run`: print the signals as JSON lines instead of posting.
- `--clock field`: use real local time (field test) instead of the API's `GET /dev/clock` (demo).
- `--ocr fast|auto|easyocr`: `fast` reads only Latin letters and Western digits (fastest). `auto` (default) also reads Arabic-Indic digits.
- `--cars`: only search for plates inside YOLO vehicle boxes. This cuts false reads from signs and other text, and costs ~+40 ms per frame.
- `--fps 4`: maximum frames processed per second.
- `--cooldown 120`: seconds before the same plate is sent again.
- `--verbose`: print every read.

`--source` also takes a video file, an image folder or a webcam index (`0`).

**Overhead camera:**

```bash
uv run --group edge python -m edge.camera.define_spots --source http://PHONE_IP:8080/shot.jpg --lot-id LAU --out edge/spots.json
```

Click the corners of each spot, press Enter after each one, then `q` to save. Spots are named
`LAU-01`, `LAU-02`… in click order; use `--ask-names` to type each name. The names must match the
`space_id`s the backend uses for that lot.

```bash
uv run --group edge python -m edge.camera.spot_watcher --source http://PHONE_IP:8080/video --spots edge/spots.json --device-id cam-overhead-1 --api http://localhost:8005
```

- Real cars: `--detector yolo` (default). It uses YOLO11n (COCO car/motorcycle/bus/truck), and a spot counts as seen occupied when boxes cover ≥ `--min-cover` 0.3 of it.
- Toy cars on a mat: `--detector diff --empty empty.jpg`. Take `empty.jpg` from `/shot.jpg` with the mat empty. Re-take it if the light changes.
- A spot changes state only when ≥ `--agree` 4 of the last `--frames` 5 frames agree. Only changes are sent; add `--emit-initial` to also send each spot's start-up state.
- `--show` opens a debug window.

## Tests and the synthetic set

```bash
uv run --group edge python -m edge.tests.synth --out edge/tests/data
```
```bash
uv run --group edge pytest edge/tests
```
```bash
uv run --group edge python -m edge.tests.eval_ocr --ocr fast auto easyocr
```

`synth` renders Lebanese-style plates: one Latin letter, then up to 6 digits, either Western or
Arabic-Indic. The plate is white with a blue left band (a stand-in, not the official design). Each plate
comes as a crop and as a whole 1280×720 "car at the gate" frame, under 5 conditions: clean, blur,
angle, dark, noise. `synth` also writes a 6 s gate video (`gate.mp4`, plate `B123456`) and an overhead
lot (4 spots, empty reference, frame sequence). `eval_ocr` writes `edge/tests/ocr_report.json`.

## Measured accuracy and speed

**Preliminary.** The full `eval_ocr` run (60 plates × 3 OCR modes) did not finish: four other build
sessions were using the same CPU. The numbers below are from smaller runs on an i5-12450H (CPU only)
while those sessions were running, so the timings are pessimistic. Re-run `eval_ocr` on an idle laptop
and replace this section with its numbers.

| Test | Result |
|---|---|
| Fast OCR, Western-digit plates (1 per condition: clean, blur, angle, dark, noise), crop and whole frame | 5/5 crops, 5/5 frames |
| Fast OCR, Arabic-Indic plates, same 5 conditions | **0/5**: this model cannot read Arabic-Indic digits |
| EasyOCR Arabic alone, Arabic-Indic crops (30) | 19/30 (63 %), ~440 ms per crop. Misses: the Latin letter read as an Arabic digit (blur/noise/angle). `--ocr auto` takes the letter from the fast OCR; that fix is **not measured yet** |
| Gate video (60 frames at 720p, `--ocr fast`) | 1 signal, `B123456`, correct; 100 ms/frame mean, 178 ms p95 |
| Image folder (each car seen once) | 0 signals, as intended (debounce needs 2 of 5 frames) |
| Spot watcher, `diff` detector, 4 spots × 4 states | all 8 transitions correct |

Synthetic plates are much easier than real Lebanese plates (dirt, glare, frames, real fonts). Treat these
numbers as an **upper bound**: real-plate accuracy is unmeasured. The attendant must be able to confirm or
correct the OCR text.
