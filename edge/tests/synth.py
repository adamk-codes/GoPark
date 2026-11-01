"""Synthetic test data for the camera edge (no real plates, no camera needed).

Renders Lebanese-style plates (one Latin letter + up to 6 digits, Western or Arabic-Indic digits), drops
them on crude car rears, and degrades them (blur, angle, dark, noise). Also renders a short "car drives up
to the gate" video and an overhead lot with spots. Deterministic for a given seed.

    uv run python -m edge.tests.synth --out edge/tests/data
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/Library/Fonts")]
FONTS = ["arialbd.ttf", "tahomabd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"]
LETTERS = "BGMNOSTZRKYJ"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
CONDITIONS = ["clean", "blur", "angle", "dark", "noise"]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        for f in FONTS:
            if (d / f).exists():
                return ImageFont.truetype(str(d / f), size)
    return ImageFont.load_default(size)


def random_plate(rng: random.Random) -> str:
    """Return the normalised truth, e.g. 'B123456'."""
    return rng.choice(LETTERS) + str(rng.randint(1, 999999))


def render_plate(truth: str, arabic_digits: bool, rng: random.Random) -> np.ndarray:
    """A 520x120 white plate with a blue left band (stand-in for the real design), BGR."""
    w, h = 520, 120
    img = Image.new("RGB", (w, h), (245, 245, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(20, 20, 20), width=5)
    d.rectangle([5, 5, 62, h - 6], fill=(25, 60, 150))
    d.polygon([(33, 30), (18, 75), (48, 75)], fill=(30, 140, 60))  # cedar-ish
    d.text((14, 82), "LB", font=_font(22), fill=(255, 255, 255))
    digits = truth[1:].translate(str.maketrans("0123456789", AR_DIGITS)) if arabic_digits else truth[1:]
    text = f"{truth[0]} {digits}"
    font = _font(rng.choice([78, 82, 86]))
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = 62 + (w - 62 - tw) // 2 - box[0]
    y = (h - th) // 2 - box[1]
    d.text((x, y), text, font=font, fill=(15, 15, 15))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def degrade(img: np.ndarray, cond: str, rng: random.Random) -> np.ndarray:
    out = img.copy()
    h, w = out.shape[:2]
    if cond == "blur":
        k = rng.choice([5, 7, 9])
        out = cv2.GaussianBlur(out, (k, k), 0)
        if rng.random() < 0.5:  # motion blur
            m = np.zeros((k, k), np.float32)
            m[k // 2, :] = 1.0 / k
            out = cv2.filter2D(out, -1, m)
    elif cond == "angle":
        dx, dy = rng.uniform(0.08, 0.18) * w, rng.uniform(0.05, 0.15) * h
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        if rng.random() < 0.5:
            dst = np.float32([[dx, dy], [w, 0], [w, h], [dx, h - dy]])
        else:
            dst = np.float32([[0, 0], [w - dx, dy], [w - dx, h - dy], [0, h]])
        out = cv2.warpPerspective(out, cv2.getPerspectiveTransform(src, dst), (w, h),
                                  borderMode=cv2.BORDER_REPLICATE)
        ang = rng.uniform(-8, 8)
        rot = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        out = cv2.warpAffine(out, rot, (w, h), borderMode=cv2.BORDER_REPLICATE)
    elif cond == "dark":
        out = np.clip(out.astype(np.float32) * rng.uniform(0.25, 0.4) + rng.uniform(0, 10), 0, 255).astype(np.uint8)
    elif cond == "noise":
        n = np.random.default_rng(rng.randint(0, 2**31)).normal(0, 22, out.shape)
        out = np.clip(out.astype(np.float32) + n, 0, 255).astype(np.uint8)
        q = rng.randint(25, 45)
        out = cv2.imdecode(cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, q])[1], cv2.IMREAD_COLOR)
    return out


def car_scene(plate: np.ndarray, rng: random.Random, size=(1280, 720), plate_w: int | None = None,
              shift: tuple[int, int] = (0, 0)) -> np.ndarray:
    """Plate mounted on a crude car rear in front of a wall/road. Plate is placed clean; degrade after."""
    W, H = size
    bg = np.zeros((H, W, 3), np.uint8)
    top, bot = np.array([rng.randint(90, 160)] * 3), np.array([rng.randint(50, 90)] * 3)
    for y in range(H):
        bg[y] = top + (bot - top) * y / H
    cv2.rectangle(bg, (0, int(H * 0.8)), (W, H), (60, 60, 64), -1)  # road
    pw = plate_w or rng.randint(int(W * 0.17), int(W * 0.24))
    ph = int(pw * plate.shape[0] / plate.shape[1])
    cx, cy = W // 2 + shift[0], int(H * 0.62) + shift[1]
    cw, ch = int(pw * 3.6), int(pw * 2.0)
    color = tuple(int(c) for c in rng.choice([(180, 180, 185), (40, 40, 40), (200, 60, 50), (240, 240, 240),
                                               (60, 90, 160)]))
    x0, y0 = cx - cw // 2, cy - int(ch * 0.62)
    cv2.rectangle(bg, (x0, y0), (x0 + cw, y0 + ch), color, -1)
    cv2.rectangle(bg, (x0 + int(cw * 0.12), y0 - int(ch * 0.35)), (x0 + int(cw * 0.88), y0), color, -1)
    cv2.rectangle(bg, (x0 + int(cw * 0.18), y0 - int(ch * 0.3)), (x0 + int(cw * 0.82), y0 - 5), (40, 45, 55), -1)
    for lx in (x0 + 10, x0 + cw - int(cw * 0.14) - 10):  # tail lights
        cv2.rectangle(bg, (lx, y0 + int(ch * 0.15)), (lx + int(cw * 0.14), y0 + int(ch * 0.35)), (30, 30, 200), -1)
    cv2.rectangle(bg, (x0 - 8, y0 + int(ch * 0.8)), (x0 + cw + 8, y0 + ch), (25, 25, 25), -1)  # bumper
    for wx in (x0 + int(cw * 0.12), x0 + int(cw * 0.88)):  # wheels
        cv2.circle(bg, (wx, y0 + ch), int(ch * 0.18), (15, 15, 15), -1)
    pr = cv2.resize(plate, (pw, ph), interpolation=cv2.INTER_AREA)
    px, py = cx - pw // 2, cy - ph // 2
    bg[py:py + ph, px:px + pw] = pr
    return bg


def make_plate_set(out: Path, seed: int = 7, per_cond: int = 6) -> list[dict]:
    """Writes crops/ (plate only) and scenes/ (whole frame) + labels.csv. Returns the label rows."""
    rng = random.Random(seed)
    (out / "crops").mkdir(parents=True, exist_ok=True)
    (out / "scenes").mkdir(parents=True, exist_ok=True)
    rows = []
    for script in ("latin", "arabic"):
        for cond in CONDITIONS:
            for i in range(per_cond):
                truth = random_plate(rng)
                plate = render_plate(truth, script == "arabic", rng)
                name = f"{script}_{cond}_{i:02d}.png"
                cv2.imwrite(str(out / "crops" / name), degrade(plate, cond, rng))
                scene = car_scene(plate, rng)
                cv2.imwrite(str(out / "scenes" / name.replace(".png", ".jpg")), degrade(scene, cond, rng),
                            [cv2.IMWRITE_JPEG_QUALITY, 90])
                rows.append({"file": name, "truth": truth, "script": script, "condition": cond})
    with open(out / "labels.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["file", "truth", "script", "condition"])
        wr.writeheader()
        wr.writerows(rows)
    return rows


def make_gate_video(path: Path, truth: str = "B123456", seed: int = 3, fps: int = 10) -> dict:
    """~6 s: empty gate, a car drives up (plate grows), waits, leaves. Some frames blurred."""
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    plate = render_plate(truth, False, rng)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1280, 720))
    empty = car_scene(plate, random.Random(seed), plate_w=10)  # tiny far-away car as "empty" frame
    empty[:] = cv2.GaussianBlur(empty, (3, 3), 0)
    n_frames = 0
    for i in range(10):
        vw.write(empty)
        n_frames += 1
    for i in range(40):
        pw = int(90 + i * 6)
        frame = car_scene(plate, random.Random(seed), plate_w=pw, shift=(int((20 - i) * 4), 0))
        if i % 7 == 3:
            frame = degrade(frame, "blur", rng)
        vw.write(frame)
        n_frames += 1
    for i in range(10):
        vw.write(empty)
        n_frames += 1
    vw.release()
    return {"file": str(path), "truth": truth, "frames": n_frames, "fps": fps}


def make_overhead_lot(out: Path, lot_id: str = "LAU", seed: int = 5) -> dict:
    """Overhead view: 4 spots, an empty reference frame and 3 frames with cars (toy-demo style).
    Writes spots.json for spot_watcher (polygon coordinates in pixels)."""
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    W, H = 960, 540
    base = np.full((H, W, 3), 85, np.uint8)
    noise = np.random.default_rng(seed).normal(0, 6, base.shape)
    base = np.clip(base + noise, 0, 255).astype(np.uint8)
    spots = []
    for n in range(4):
        x0 = 80 + n * 210
        poly = [[x0, 120], [x0 + 180, 120], [x0 + 180, 440], [x0, 440]]
        cv2.polylines(base, [np.array(poly, np.int32)], True, (235, 235, 235), 4)
        spots.append({"space_id": f"{lot_id}-{n + 1:02d}", "polygon": poly})
    cv2.imwrite(str(out / "empty.jpg"), base)
    occupancy = [[1, 0, 0, 1], [1, 1, 0, 1], [0, 1, 0, 1]]
    for k, occ in enumerate(occupancy):
        img = base.copy()
        for n, o in enumerate(occ):
            if o:
                x0 = 80 + n * 210 + rng.randint(15, 30)
                color = tuple(int(c) for c in rng.choice([(40, 40, 200), (200, 200, 200), (30, 30, 30), (160, 90, 40)]))
                cv2.rectangle(img, (x0, 150), (x0 + 130, 410), color, -1)
                cv2.rectangle(img, (x0 + 15, 200), (x0 + 115, 260), (50, 60, 70), -1)  # windscreen
        cv2.imwrite(str(out / f"frame_{k}.jpg"), img)
    cfg = {"lot_id": lot_id, "frame_size": [W, H], "spots": spots}
    (out / "spots.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return {"spots": cfg, "occupancy": occupancy}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="edge/tests/data")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-cond", type=int, default=6, help="plates per (script, condition)")
    a = ap.parse_args()
    out = Path(a.out)
    rows = make_plate_set(out / "plates", a.seed, a.per_cond)
    vid = make_gate_video(out / "gate.mp4")
    lot = make_overhead_lot(out / "overhead")
    print(f"{len(rows)} plates -> {out / 'plates'}; video {vid['file']} ({vid['frames']} frames, "
          f"plate {vid['truth']}); overhead lot with {len(lot['spots']['spots'])} spots -> {out / 'overhead'}")


if __name__ == "__main__":
    main()
