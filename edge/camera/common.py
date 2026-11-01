"""Shared pieces of the camera edge: plate normalisation, debouncing, clock, frame sources, signal output.

Everything here is light (stdlib; cv2 only inside `frames`) so the tests and sensor_sim run without the edge group.
Frames are processed in memory only; nothing in this package writes images to disk.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[2]
SIGNAL_SCHEMA = ROOT / "contract" / "schemas" / "signal.schema.json"

# ---------- http (stdlib, so the default install is enough) ----------
def http_json(method: str, url: str, body=None, params: dict | None = None, timeout: float = 5.0):
    """JSON request; returns the decoded body. Raises urllib.error.HTTPError (has `.code`) on 4xx/5xx."""
    import urllib.parse
    import urllib.request
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


# ---------- plate normalisation ----------
# Arabic-Indic (U+0660..) and Eastern Arabic-Indic / Persian (U+06F0..) digits -> 0-9.
_DIGITS = {**{0x0660 + i: str(i) for i in range(10)}, **{0x06F0 + i: str(i) for i in range(10)}}
PLATE_RE = re.compile(r"^[A-Z]\d{1,6}$")
# OCR confusions, fixed by position: a Lebanese plate is one letter then digits only.
_AS_LETTER = str.maketrans({"0": "O", "2": "Z", "5": "S", "6": "G", "8": "B"})
_AS_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "J": "1", "T": "7",
                           "Z": "2", "S": "5", "G": "6", "B": "8", "A": "4"})


def normalize_plate(text: str | None) -> str | None:
    """Uppercase, Arabic-Indic -> Western digits, drop spaces/dashes/dots. Same rule as api.db.normalize_plate."""
    if not text:
        return None
    s = re.sub(r"[\s\-_.·|]", "", text.translate(_DIGITS)).upper()
    return s or None


def plate_from_ocr(text: str | None) -> str | None:
    """Normalise OCR output and return a valid plate (`^[A-Z]\\d{1,6}$`) or None.

    Applies positional confusion fixes (first char must be a letter, the rest digits) and drops
    non-alphanumeric noise such as the cedar or a border read as a character.
    """
    s = normalize_plate(text)
    if not s:
        return None
    s = re.sub(r"[^A-Z0-9]", "", s)
    if len(s) < 2:
        return None
    fixed = s[0].translate(_AS_LETTER) + s[1:].translate(_AS_DIGIT)
    if PLATE_RE.match(fixed):
        return fixed
    # One char too many: OCR read the border or a screw as a character; try dropping one end.
    if len(s) == 8:
        for cand in (fixed[1:], fixed[:-1]):
            c = cand[0].translate(_AS_LETTER) + cand[1:].translate(_AS_DIGIT)
            if PLATE_RE.match(c):
                return c
    return None


# ---------- debounce ----------
class PlateDebouncer:
    """Confirm a plate once it is read in >= `need` of the last `window` frames.

    After confirming, the same plate is not confirmed again for `cooldown_s` seconds (a car waiting at the
    barrier must not produce one signal per frame). Time is passed in so tests and video files work.
    """

    def __init__(self, window: int = 5, need: int = 2, cooldown_s: float = 120.0):
        self.window, self.need, self.cooldown_s = window, need, cooldown_s
        self.frames: deque[set[str]] = deque(maxlen=window)
        self.last_sent: dict[str, float] = {}

    def push(self, plates: set[str], t: float) -> list[str]:
        self.frames.append(set(plates))
        out = []
        for p in sorted(plates):
            seen = sum(p in f for f in self.frames)
            if seen >= self.need and t - self.last_sent.get(p, float("-inf")) >= self.cooldown_s:
                self.last_sent[p] = t
                out.append(p)
        return out


# ---------- clock ----------
class Clock:
    """`observed_at` source. demo: the API's demo clock (`GET /dev/clock`), re-synced every `resync_s`;
    field: the laptop's real local time with UTC offset."""

    def __init__(self, mode: str, api: str | None = None, resync_s: float = 20.0):
        if mode not in ("demo", "field"):
            raise ValueError("clock mode must be demo or field")
        self.mode, self.api, self.resync_s = mode, api, resync_s
        self._base: datetime | None = None
        self._at = 0.0

    def now(self) -> datetime:
        if self.mode == "field":
            return datetime.now().astimezone()
        if self._base is None or time.monotonic() - self._at > self.resync_s:
            self._sync()
        return self._base + timedelta(seconds=time.monotonic() - self._at)

    def iso(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def _sync(self) -> None:
        try:
            now = http_json("GET", f"{self.api}/dev/clock", timeout=3)["now"]
            self._base, self._at = datetime.fromisoformat(now), time.monotonic()
        except Exception as e:  # keep the old offset if we had one, else fall back to real time
            if self._base is None:
                print(f"[clock] cannot read {self.api}/dev/clock ({e}); using local time", file=sys.stderr)
                self._base, self._at = datetime.now().astimezone(), time.monotonic()


# ---------- signals ----------
_validator = None


def validate_signal(sig: dict) -> None:
    """Raise jsonschema.ValidationError if `sig` does not match contract/schemas/signal.schema.json."""
    global _validator
    if _validator is None:
        import jsonschema
        schema = json.loads(SIGNAL_SCHEMA.read_text(encoding="utf-8"))
        _validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    _validator.validate(sig)


def make_signal(*, device_id: str, lot_id: str, source: str, kind: str, observed_at: str, payload: dict) -> dict:
    sig = {"client_event_id": str(uuid.uuid4()), "device_id": device_id, "lot_id": lot_id,
           "source": source, "kind": kind, "observed_at": observed_at, "payload": payload}
    validate_signal(sig)
    return sig


class SignalSink:
    """Prints signals (dry run) or POSTs them to `{api}/signals`. Failed posts stay in a small in-memory
    outbox and are retried with the next signal (client_event_id makes the retry idempotent)."""

    def __init__(self, api: str | None, dry_run: bool, out=None):
        self.api, self.dry_run = api, dry_run
        self.out = out or sys.stdout
        self.outbox: deque[dict] = deque(maxlen=500)
        self.sent: list[dict] = []

    def send(self, sig: dict) -> None:
        self.sent.append(sig)
        if self.dry_run:
            print(json.dumps(sig, ensure_ascii=False), file=self.out, flush=True)
            return
        self.outbox.append(sig)
        self.flush()

    def flush(self) -> None:
        if self.dry_run or not self.outbox:
            return
        batch = list(self.outbox)
        try:
            res = http_json("POST", f"{self.api}/signals", {"signals": batch})
            self.outbox.clear()
            print(f"[post] {len(batch)} signal(s) -> {res}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[post] failed, {len(batch)} queued: {e}", file=sys.stderr, flush=True)


# ---------- frame sources ----------
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def is_live(source: str) -> bool:
    return source.isdigit() or source.split(":", 1)[0].lower() in ("http", "https", "rtsp", "rtmp")


def frames(source: str, every: int = 1, max_fps: float | None = None,
           realtime: bool = False) -> Iterator[tuple[float, "object"]]:
    """Yield `(t_seconds, BGR frame)` from a webcam index, stream URL, video file or image folder.

    Video files and folders use media time (frame index / fps) so debouncing behaves like live;
    `realtime` also waits so they play at that speed (the sensor trigger works in wall time).
    Live streams use wall time and always hand out the newest frame (stale buffered frames are dropped).
    """
    if realtime and not is_live(source):
        t0 = time.monotonic()
        for t, img in frames(source, every, max_fps):
            time.sleep(max(0.0, t - (time.monotonic() - t0)))
            yield t, img
        return
    import cv2
    p = Path(source)
    if p.is_dir():
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT)
        fps = max_fps or 2.0
        for i, f in enumerate(files[::every]):
            img = cv2.imread(str(f))
            if img is not None:
                yield i / fps, img
        return
    if p.is_file() and p.suffix.lower() in IMAGE_EXT:
        img = cv2.imread(str(p))
        if img is not None:
            yield 0.0, img
        return
    if is_live(source):
        yield from _live_frames(int(source) if source.isdigit() else source, max_fps)
        return
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        raise SystemExit(f"cannot open source {source!r}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    i = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        if i % every == 0:
            yield i / fps, img
        i += 1
    cap.release()


def _live_frames(src, max_fps: float | None):
    import cv2
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"cannot open stream {src!r} (is the phone app running and on the same Wi-Fi?)")
    latest: dict = {"img": None, "n": 0}
    stop = threading.Event()

    def grab():
        while not stop.is_set():
            ok, img = cap.read()
            if not ok:
                time.sleep(0.2)
                continue
            latest["img"], latest["n"] = img, latest["n"] + 1

    threading.Thread(target=grab, daemon=True).start()
    t0, last_n, gap = time.monotonic(), 0, 1.0 / max_fps if max_fps else 0.0
    try:
        while True:
            if latest["n"] == last_n:
                time.sleep(0.01)
                continue
            last_n = latest["n"]
            start = time.monotonic()
            yield start - t0, latest["img"]
            if gap:
                time.sleep(max(0.0, gap - (time.monotonic() - start)))
    finally:
        stop.set()
        cap.release()


class Timer:
    """Per-frame timing (ms) for the report."""

    def __init__(self):
        self.ms: list[float] = []

    def add(self, seconds: float) -> None:
        self.ms.append(seconds * 1000)

    def summary(self) -> dict:
        if not self.ms:
            return {"frames": 0}
        s = sorted(self.ms)
        return {"frames": len(s), "mean_ms": round(sum(s) / len(s), 1), "p50_ms": round(s[len(s) // 2], 1),
                "p95_ms": round(s[min(len(s) - 1, int(len(s) * 0.95))], 1)}
