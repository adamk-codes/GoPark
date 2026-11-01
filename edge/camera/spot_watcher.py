"""Overhead spot watcher: car-in-polygon over N frames -> `camera_spot` occupied / vacant signals.

    uv run python -m edge.camera.spot_watcher --source http://PHONE_IP:8080/video \
        --spots edge/spots.json --device-id cam-overhead-1 --dry-run

Spots come from a JSON file (make one with `python -m edge.camera.define_spots`):
    {"lot_id": "LAU", "frame_size": [W, H], "spots": [{"space_id": "LAU-01", "polygon": [[x, y], ...]}]}

Detectors:
  yolo  (default) Ultralytics YOLO, COCO classes car/motorcycle/bus/truck; a spot is "seen occupied" in a
        frame when car boxes cover >= --min-cover of its polygon.
  diff  compare each polygon with an empty-lot reference image (--empty); for toy cars on a printed mat,
        where YOLO may not recognise the toys. Sensitive to lighting changes; re-take the reference.

A spot changes state only when >= --agree of the last --frames frames agree, and only changes are sent
(start-up state is sent too with --emit-initial). Frames stay in memory; nothing is saved.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

from edge.camera.common import Clock, SignalSink, Timer, frames, make_signal

MODELS = Path(__file__).resolve().parents[1] / "models"
VEHICLE_CLASSES = [2, 3, 5, 7]  # COCO: car, motorcycle, bus, truck


def load_spots(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if not cfg.get("spots"):
        raise SystemExit(f"{path}: no spots defined")
    for s in cfg["spots"]:
        if len(s.get("polygon", [])) < 3 or not s.get("space_id"):
            raise SystemExit(f"{path}: every spot needs a space_id and >= 3 polygon points")
    return cfg


class SpotGeometry:
    """Polygon masks at the actual frame size (scaled from the JSON's frame_size if they differ)."""

    def __init__(self, cfg: dict, frame_shape: tuple[int, int]):
        import cv2
        h, w = frame_shape[:2]
        fw, fh = cfg.get("frame_size") or (w, h)
        sx, sy = w / fw, h / fh
        self.ids, self.masks, self.polys = [], [], []
        for s in cfg["spots"]:
            poly = np.array([[x * sx, y * sy] for x, y in s["polygon"]], np.int32)
            m = np.zeros((h, w), np.uint8)
            cv2.fillPoly(m, [poly], 1)
            self.ids.append(s["space_id"])
            self.masks.append(m.astype(bool))
            self.polys.append(poly)
        self.areas = [max(1, int(m.sum())) for m in self.masks]

    def coverage_from_boxes(self, boxes: np.ndarray) -> list[float]:
        """Fraction of each polygon covered by the union of the boxes (x1, y1, x2, y2)."""
        if len(boxes) == 0:
            return [0.0] * len(self.ids)
        h, w = self.masks[0].shape
        union = np.zeros((h, w), bool)
        for x1, y1, x2, y2 in boxes.astype(int):
            union[max(0, y1):min(h, y2), max(0, x1):min(w, x2)] = True
        return [float((m & union).sum()) / a for m, a in zip(self.masks, self.areas)]

    def coverage_from_diff(self, changed: np.ndarray) -> list[float]:
        return [float((m & changed).sum()) / a for m, a in zip(self.masks, self.areas)]


class SpotTracker:
    """Per-spot vote over the last `frames` observations; returns state changes."""

    def __init__(self, ids: list[str], frames: int = 5, agree: int = 4, emit_initial: bool = False):
        if agree > frames:
            raise ValueError("--agree cannot exceed --frames")
        self.agree, self.emit_initial = agree, emit_initial
        self.hist = {i: deque(maxlen=frames) for i in ids}
        self.state: dict[str, str | None] = {i: None for i in ids}

    def push(self, seen: dict[str, bool]) -> list[tuple[str, str]]:
        changes = []
        for sid, occ in seen.items():
            h = self.hist[sid]
            h.append(occ)
            n_occ = sum(h)
            new = "occupied" if n_occ >= self.agree else "vacant" if len(h) - n_occ >= self.agree else None
            if new and new != self.state[sid]:
                if self.state[sid] is not None or self.emit_initial:
                    changes.append((sid, new))
                self.state[sid] = new
        return changes


class YoloCars:
    def __init__(self, weights: str, imgsz: int = 640, conf: float = 0.3):
        from ultralytics import YOLO
        MODELS.mkdir(exist_ok=True)
        w = Path(weights)
        if not w.is_absolute() and w.parent == Path("."):
            w = MODELS / w  # keep downloaded weights in edge/models/, not the cwd
        self.model, self.imgsz, self.conf = YOLO(str(w)), imgsz, conf

    def boxes(self, img) -> np.ndarray:
        r = self.model.predict(img, imgsz=self.imgsz, conf=self.conf, classes=VEHICLE_CLASSES, verbose=False)[0]
        return r.boxes.xyxy.cpu().numpy()


class DiffDetector:
    def __init__(self, empty_path: str, threshold: int = 35):
        import cv2
        ref = cv2.imread(empty_path)
        if ref is None:
            raise SystemExit(f"cannot read --empty {empty_path}")
        self.ref, self.threshold = self._prep(ref), threshold

    @staticmethod
    def _prep(img):
        import cv2
        return cv2.GaussianBlur(img, (7, 7), 0)

    def changed(self, img) -> np.ndarray:
        """Pixels whose largest per-channel difference to the reference exceeds the threshold (colour, not
        grey: a brown car on grey tarmac has almost the same brightness)."""
        import cv2
        g = self._prep(img)
        ref = self.ref if self.ref.shape == g.shape else cv2.resize(self.ref, (g.shape[1], g.shape[0]))
        return cv2.absdiff(g, ref).max(axis=2) > self.threshold


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="stream URL, webcam index, video file or image folder")
    ap.add_argument("--spots", required=True, help="spots JSON (see define_spots)")
    ap.add_argument("--lot-id", help="defaults to lot_id in the spots file")
    ap.add_argument("--device-id", default="cam-overhead-1")
    ap.add_argument("--api", default="http://localhost:8005")
    ap.add_argument("--clock", choices=["demo", "field"], default="demo",
                    help="demo: observed_at from GET /dev/clock; field: real local time")
    ap.add_argument("--dry-run", action="store_true", help="print signals instead of posting")
    ap.add_argument("--detector", choices=["yolo", "diff"], default="yolo")
    ap.add_argument("--weights", default="yolo11n.pt", help="Ultralytics weights (downloaded to edge/models/)")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--empty", help="empty-lot reference image for --detector diff")
    ap.add_argument("--diff-threshold", type=int, default=35)
    ap.add_argument("--min-cover", type=float, default=0.3, help="polygon fraction that must be covered")
    ap.add_argument("--frames", type=int, default=5, help="vote window")
    ap.add_argument("--agree", type=int, default=4, help="frames in the window that must agree")
    ap.add_argument("--emit-initial", action="store_true", help="also send each spot's start-up state")
    ap.add_argument("--fps", type=float, default=2.0, help="max frames/s to process (live) or folder fps")
    ap.add_argument("--every", type=int, default=1, help="video files: use every Nth frame")
    ap.add_argument("--show", action="store_true", help="debug window with polygons (needs a display)")
    a = ap.parse_args(argv)

    cfg = load_spots(a.spots)
    lot_id = a.lot_id or cfg.get("lot_id")
    if not lot_id:
        raise SystemExit("--lot-id is required when the spots file has no lot_id")
    if a.detector == "diff" and not a.empty:
        raise SystemExit("--detector diff needs --empty <reference image>")
    det = YoloCars(a.weights, a.imgsz, a.conf) if a.detector == "yolo" else DiffDetector(a.empty, a.diff_threshold)
    clock = Clock(a.clock, a.api)
    sink = SignalSink(a.api, a.dry_run)
    timer = Timer()
    geo = tracker = None
    try:
        for t, img in frames(a.source, a.every, a.fps):
            if geo is None:
                geo = SpotGeometry(cfg, img.shape)
                tracker = SpotTracker(geo.ids, a.frames, a.agree, a.emit_initial)
            start = time.perf_counter()
            cover = (geo.coverage_from_boxes(det.boxes(img)) if a.detector == "yolo"
                     else geo.coverage_from_diff(det.changed(img)))
            timer.add(time.perf_counter() - start)
            seen = {sid: c >= a.min_cover for sid, c in zip(geo.ids, cover)}
            for sid, kind in tracker.push(seen):
                c = cover[geo.ids.index(sid)]
                sink.send(make_signal(device_id=a.device_id, lot_id=lot_id, source="camera_spot", kind=kind,
                                      observed_at=clock.iso(), payload={"space_id": sid, "coverage": round(c, 2)}))
            if a.show:
                _show(img, geo, seen)
    except KeyboardInterrupt:
        pass
    sink.flush()
    stats = {"detector": a.detector, "signals": len(sink.sent), "per_frame": timer.summary(),
             "state": tracker.state if tracker else {}}
    print(json.dumps(stats), file=sys.stderr)
    return stats


def _show(img, geo: SpotGeometry, seen: dict[str, bool]) -> None:
    import cv2
    vis = img.copy()
    for sid, poly in zip(geo.ids, geo.polys):
        col = (0, 0, 255) if seen[sid] else (0, 200, 0)
        cv2.polylines(vis, [poly], True, col, 2)
        cv2.putText(vis, sid, tuple(int(v) for v in poly[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
    cv2.imshow("spot_watcher (q to quit)", vis)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        raise KeyboardInterrupt


if __name__ == "__main__":
    main()
