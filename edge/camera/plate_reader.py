"""Entrance/exit plate reader: frames -> plate detector -> OCR -> normalise -> debounce -> `camera_plate`.

    uv run --group edge python -m edge.camera.plate_reader --source http://PHONE_IP:8080/video \
        --lot-id AUB --device-id cam-entrance-1 --kind enter --dry-run

Pipeline per frame (all in memory; no image is ever written to disk):
  1. optional --roi crop (the lane in front of the barrier) and optional --cars gate: Ultralytics YOLO finds
     vehicles and plates are only searched inside vehicle boxes;
  2. plate detector: open-image-models YOLOv9-tiny ONNX (fast-alpr);
  3. OCR (--ocr): `fast` = fast-plate-ocr CCT-XS ONNX (Latin letters + Western digits, ~ms per crop);
     `auto` (default) = fast first, EasyOCR Arabic+English fallback when the fast read is not a valid
     plate or is unsure (needed for Arabic-Indic digits); `easyocr` = EasyOCR only;
  4. normalise (uppercase, Arabic-Indic -> 0-9, strip spaces/dashes, positional O/0 B/8 fixes) and keep
     only `^[A-Z]\\d{1,6}$`;
  5. always-on (default): a plate is sent once it is read in >= 2 of the last 5 processed frames, then not
     again for --cooldown seconds.
     --trigger listen|api: frames are still read all the time, but a plate is sent only for a gate_sensor
     event: the best read within +-8 s of it, with the event's direction (--kind auto) and gate_id/lane.
     See edge/camera/trigger.py and edge/INTEGRATION.md.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from edge.camera.common import Clock, PlateDebouncer, SignalSink, Timer, frames, make_signal, plate_from_ocr
from edge.camera.trigger import PlateHit

MODELS = Path(__file__).resolve().parents[1] / "models"
VEHICLE_CLASSES = [2, 3, 5, 7]  # COCO: car, motorcycle, bus, truck
_LATIN = re.compile(r"[A-Z]")
EASY_ALLOW = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789٠١٢٣٤٥٦٧٨٩"


@dataclass
class Read:
    plate: str | None
    conf: float
    engine: str
    raw: str = ""


class PlateOCR:
    """OCR for one plate crop (BGR). See module docstring for the modes."""

    def __init__(self, mode: str = "auto", fast_model: str = "cct-xs-v2-global-model", min_conf: float = 0.85,
                 band: float = 0.12):
        self.mode, self.min_conf, self.band = mode, min_conf, band
        self.fast = self.easy = None
        if mode in ("fast", "auto"):
            from fast_alpr.default_ocr import DefaultOCR
            self.fast = DefaultOCR(hub_ocr_model=fast_model, device="cpu")
        if mode in ("easyocr", "auto"):
            self._easy_init()

    def _easy_init(self):
        import easyocr
        self.easy = easyocr.Reader(["ar", "en"], gpu=False, verbose=False)

    def read(self, crop: np.ndarray) -> Read:
        fast = self._fast(crop) if self.fast else None
        if fast and (self.mode == "fast" or (fast.plate and fast.conf >= self.min_conf)):
            return fast
        easy = self._easy(crop, fast)
        if fast and fast.plate and (not easy.plate or fast.conf >= easy.conf):
            return fast
        return easy if easy.plate or not fast else fast

    def _fast(self, crop) -> Read:
        r = self.fast.predict(crop)
        if r is None or not r.text:
            return Read(None, 0.0, "fast")
        text = r.text.replace("_", "")
        conf = r.confidence
        conf = statistics.mean(conf[:max(1, len(text))]) if isinstance(conf, list) else float(conf or 0)
        return Read(plate_from_ocr(text), float(conf), "fast", text)

    def _easy(self, crop, fast: Read | None) -> Read:
        import cv2
        w = crop.shape[1]
        g = cv2.cvtColor(crop[:, int(w * self.band):], cv2.COLOR_BGR2GRAY)  # drop the left flag band
        res = self.easy.recognize(g, allowlist=EASY_ALLOW, detail=1)
        if not res:
            return Read(None, 0.0, "easyocr")
        text = "".join(x[1] for x in res).replace(" ", "")
        conf = float(min(x[2] for x in res))
        plate = plate_from_ocr(text) if text and _LATIN.match(text[0]) else None
        if plate is None and text:
            m = _LATIN.search(text)
            if m:  # leading junk before the letter (often the band edge)
                plate = plate_from_ocr(text[m.start():])
            elif fast and fast.raw and plate_from_ocr(fast.raw[0] + "1"):
                # The Arabic model read the Latin letter as an Arabic digit: take the letter from the fast
                # (Latin) model and the digits from EasyOCR.
                plate = plate_from_ocr(fast.raw[0] + text[1:])
        return Read(plate, conf, "easyocr+fast" if plate and not _LATIN.match(text[0]) else "easyocr", text)


class PlateReader:
    def __init__(self, ocr: PlateOCR, detector_model: str = "yolo-v9-t-384-license-plate-end2end",
                 det_conf: float = 0.4, cars: bool = False, car_weights: str = "yolo11n.pt",
                 roi: tuple[int, int, int, int] | None = None):
        from fast_alpr.default_detector import DefaultDetector
        self.det = DefaultDetector(model_name=detector_model, conf_thresh=det_conf,
                                   providers=["CPUExecutionProvider"])
        self.ocr, self.roi = ocr, roi
        self.cars = None
        if cars:
            from ultralytics import YOLO
            MODELS.mkdir(exist_ok=True)
            w = Path(car_weights)
            self.cars = YOLO(str(MODELS / w if w.parent == Path(".") else w))

    def regions(self, img: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
        """(x_off, y_off, sub-image) to search for plates."""
        x0 = y0 = 0
        if self.roi:
            x0, y0, w, h = self.roi
            img = img[y0:y0 + h, x0:x0 + w]
        if self.cars is None:
            return [(x0, y0, img)]
        r = self.cars.predict(img, imgsz=640, conf=0.3, classes=VEHICLE_CLASSES, verbose=False)[0]
        out = []
        H, W = img.shape[:2]
        for x1, y1, x2, y2 in r.boxes.xyxy.cpu().numpy().astype(int):
            pad = int(0.05 * (x2 - x1))
            x1, y1, x2, y2 = max(0, x1 - pad), max(0, y1 - pad), min(W, x2 + pad), min(H, y2 + pad)
            out.append((x0 + x1, y0 + y1, img[y1:y2, x1:x2]))
        return out

    def read_frame(self, img: np.ndarray) -> list[Read]:
        return self.read_frame_with_cars(img)[0]

    def read_frame_with_cars(self, img: np.ndarray) -> tuple[list[Read], list[np.ndarray]]:
        """OCR plate detections and return vehicle regions for an optional in-memory matcher."""
        reads = []
        regions = self.regions(img)
        for _, _, sub in regions:
            for d in self.det.predict(sub):
                b = d.bounding_box
                x1, y1 = max(0, b.x1), max(0, b.y1)
                crop = sub[y1:b.y2, x1:b.x2]
                if crop.size == 0 or crop.shape[1] < 20:
                    continue
                reads.append(self.ocr.read(crop))
        return reads, [sub for _, _, sub in regions]


def main(argv: list[str] | None = None) -> dict:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="stream URL, webcam index, video file, image or folder")
    ap.add_argument("--lot-id", required=True)
    ap.add_argument("--device-id", default="cam-entrance-1")
    ap.add_argument("--kind", choices=["enter", "exit", "auto"], default="enter",
                    help="enter: entrance cam, exit: exit cam, auto: direction from the gate sensor (needs --trigger)")
    ap.add_argument("--api", default="http://localhost:8005")
    ap.add_argument("--clock", choices=["demo", "field"], default="demo",
                    help="demo: observed_at from GET /dev/clock; field: real local time")
    ap.add_argument("--dry-run", action="store_true", help="print signals instead of posting")
    ap.add_argument("--ocr", choices=["auto", "fast", "easyocr"], default="auto")
    ap.add_argument("--min-conf", type=float, default=0.85, help="auto: below this the fast read goes to EasyOCR")
    ap.add_argument("--cars", action="store_true", help="only search plates inside YOLO vehicle boxes")
    ap.add_argument("--car-weights", default="yolo11n.pt")
        ap.add_argument("--vehicle-match", action="store_true",
                        help="suggest expected vehicles with zero-shot CLIP when the gate plate is unreadable")
        ap.add_argument("--clip-model", default="openai/clip-vit-base-patch32", help="Hugging Face CLIP model id")
    ap.add_argument("--roi", help="x,y,w,h crop of the lane, in pixels")
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--need", type=int, default=2, help="frames a plate must be read in before it is sent")
    ap.add_argument("--cooldown", type=float, default=120.0, help="seconds before the same plate is sent again")
    ap.add_argument("--trigger", choices=["none", "listen", "api"], default="none",
                    help="none: always on (default); listen/api: post only the best read around gate_sensor events")
    ap.add_argument("--listen", default="0.0.0.0:8090", help="--trigger listen: host:port for POST /gate")
    ap.add_argument("--window-s", type=float, default=8.0, help="--trigger: +- seconds around a gate event")
    ap.add_argument("--gate-id", help="--trigger: only events from this gate_id")
    ap.add_argument("--lane", help="--trigger: only events from this lane")
    ap.add_argument("--fps", type=float, default=4.0, help="max frames/s to process (live) or folder fps")
    ap.add_argument("--every", type=int, default=1, help="video files: use every Nth frame")
    ap.add_argument("--realtime", action="store_true", help="files: play at recorded speed (for --trigger tests)")
    ap.add_argument("--loop", action="store_true", help="files: start again at the end (until Ctrl+C)")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--verbose", action="store_true", help="print every per-frame read to stderr")
    a = ap.parse_args(argv)
    if a.kind == "auto" and a.trigger == "none":
        ap.error("--kind auto needs --trigger listen or --trigger api (the direction comes from the gate sensor)")
    if a.vehicle_match and (a.trigger == "none" or not a.cars):
        ap.error("--vehicle-match requires --cars and --trigger listen|api")

    roi = tuple(int(v) for v in a.roi.split(",")) if a.roi else None
    t0 = time.perf_counter()
    reader = PlateReader(PlateOCR(a.ocr, min_conf=a.min_conf), cars=a.cars or a.vehicle_match,
                         car_weights=a.car_weights, roi=roi)
    print(f"[plate_reader] models loaded in {time.perf_counter() - t0:.1f}s", file=sys.stderr)
    clock = Clock(a.clock, a.api)
    sink = SignalSink(a.api, a.dry_run)
    deb = PlateDebouncer(a.window, a.need, a.cooldown)
    timer = Timer()
    best: dict[str, float] = {}
    pairer = events = None
    vehicle_matcher = None
    car_buffer: deque[tuple[float, np.ndarray]] = deque()
    if a.vehicle_match:
        from edge.camera.vehicle_matcher import load_matcher
        vehicle_matcher = load_matcher(a.clip_model)
    if a.trigger != "none":
        from edge.camera.trigger import ApiPollSource, GatePairer, ListenSource
        pairer = GatePairer(a.lot_id, a.window_s, a.need, a.kind, a.gate_id, a.lane)
        if a.trigger == "listen":
            host, port = a.listen.rsplit(":", 1)
            events = ListenSource(host, int(port))
        else:
            events = ApiPollSource(a.api, a.lot_id)
    stats = {"ocr": a.ocr, "cars": a.cars, "trigger": a.trigger, "gate_events": 0, "unpaired_events": 0}

    def send_pairings(done):
        for pr in done:
            stats["gate_events"] += 1
            if pr.plate is None:
                stats["unpaired_events"] += 1
                print(f"[trigger] {pr.event.kind} at {pr.event.gate_id}: no plate read >= {a.need}x "
                      f"within +-{a.window_s:g}s {pr.candidates}", file=sys.stderr)
                if vehicle_matcher is not None:
                    from edge.camera.common import http_json

                    observed_at = pr.event.observed_at or clock.iso()
                    try:
                        expected = http_json("GET", f"{a.api}/lots/{a.lot_id}/expected_vehicles",
                                             params={"around": observed_at, "window_min": 20})
                    except Exception as exc:
                        print(f"[vehicle_match] expected vehicles unavailable: {exc}", file=sys.stderr)
                        continue
                    crops = [(stamp, crop) for stamp, crop in car_buffer
                             if abs(stamp - pr.event.t) <= a.window_s]
                    if not crops or not expected:
                        print(f"[vehicle_match] skipped: {len(crops)} car crop(s), "
                              f"{len(expected or [])} expected vehicle(s)", file=sys.stderr)
                        continue
                    ranked = []
                    for stamp, crop in crops:
                        result = vehicle_matcher.match(crop, expected)
                        if result and result.candidates:
                            ranked.append((result.candidates[0]["score"] + result.top_margin, stamp, result))
                    if not ranked:
                        continue
                    _, used_stamp, result = max(ranked, key=lambda row: row[0])
                    car_buffer_items = [(stamp, crop) for stamp, crop in car_buffer if stamp != used_stamp]
                    car_buffer.clear()
                    car_buffer.extend(car_buffer_items)
                    payload = result.as_payload(a.clip_model)
                    payload.update({k: v for k, v in (("gate_id", pr.event.gate_id), ("lane", pr.event.lane))
                                    if v is not None})
                    sink.send(make_signal(device_id=a.device_id, lot_id=a.lot_id, source="camera_vehicle",
                                          kind=pr.event.kind, observed_at=observed_at, payload=payload))
                    stats["vehicle_suggestions"] = stats.get("vehicle_suggestions", 0) + 1
                continue
            payload = {"plate_norm": pr.plate, "ocr_conf": round(pr.conf, 2), "reads": pr.reads}
            for k, v in (("gate_id", pr.event.gate_id), ("lane", pr.event.lane)):
                if v is not None:
                    payload[k] = v
            sink.send(make_signal(device_id=a.device_id, lot_id=a.lot_id, source="camera_plate",
                                  kind=pr.event.kind, observed_at=pr.observed_at, payload=payload))

    n = 0
    try:
        while True:
            for t, img in frames(a.source, a.every, a.fps, realtime=a.realtime):
                start = time.perf_counter()
                if vehicle_matcher is not None:
                    reads, crops = reader.read_frame_with_cars(img)
                    frame_t = time.monotonic()
                    car_buffer.extend((frame_t, crop) for crop in crops)
                    keep_after = frame_t - max(30.0, a.window_s * 4)
                    while car_buffer and car_buffer[0][0] < keep_after:
                        car_buffer.popleft()
                else:
                    reads = reader.read_frame(img)
                timer.add(time.perf_counter() - start)
                if a.verbose:
                    print(f"[frame {n} t={t:.1f}] " + ", ".join(f"{r.raw!r}->{r.plate} {r.conf:.2f} {r.engine}"
                                                                for r in reads), file=sys.stderr)
                if pairer is not None:
                    now, at = time.monotonic(), clock.iso()
                    pairer.add_hits([PlateHit(now, r.plate, r.conf, at) for r in reads if r.plate])
                    for ev in events.poll():
                        pairer.add_event(ev)
                    send_pairings(pairer.due(now))
                else:
                    plates = {r.plate for r in reads if r.plate}
                    for r in reads:
                        if r.plate:
                            best[r.plate] = max(best.get(r.plate, 0.0), r.conf)
                    for p in deb.push(plates, t):
                        sink.send(make_signal(device_id=a.device_id, lot_id=a.lot_id, source="camera_plate",
                                              kind=a.kind, observed_at=clock.iso(),
                                              payload={"plate_norm": p, "ocr_conf": round(best.get(p, 0.0), 2)}))
                n += 1
                if a.max_frames and n >= a.max_frames:
                    raise KeyboardInterrupt
            if not a.loop:
                break
    except KeyboardInterrupt:
        pass
    if pairer is not None:
        for ev in events.poll():
            pairer.add_event(ev)
        send_pairings(pairer.due(time.monotonic(), flush=True))
        events.close()
    sink.flush()
    stats.update({"signals": len(sink.sent), "plates": [s["payload"]["plate_norm"] for s in sink.sent
                                                           if "plate_norm" in s["payload"]],
                  "per_frame": timer.summary()})
    print(json.dumps(stats), file=sys.stderr)
    return stats


if __name__ == "__main__":
    main()
