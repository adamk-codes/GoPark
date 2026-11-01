"""Measure plate OCR accuracy and speed on the synthetic set (edge/tests/synth.py).

    uv run --group edge python -m edge.tests.synth --out edge/tests/data
    uv run --group edge python -m edge.tests.eval_ocr --data edge/tests/data/plates --ocr fast auto

For each OCR mode: exact-match accuracy of the normalised plate on
  crops  (plate image only: OCR + normalisation), and
  scenes (whole 1280x720 frame: plate detector + OCR + normalisation),
split by digit script (latin / arabic) and condition (clean / blur / angle / dark / noise), plus per-image
time. Writes edge/tests/ocr_report.json. Synthetic plates are much easier than real ones: treat these
numbers as an upper bound, not as field accuracy.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import time
from collections import defaultdict
from pathlib import Path

import cv2

from edge.camera.plate_reader import PlateOCR, PlateReader


def _pct(ok: int, n: int) -> float:
    return round(100 * ok / n, 1) if n else 0.0


def evaluate(data: Path, mode: str) -> dict:
    rows = list(csv.DictReader(open(data / "labels.csv", encoding="utf-8")))
    reader = PlateReader(PlateOCR(mode))
    out: dict = {"mode": mode}
    for kind in ("crops", "scenes"):
        tally = defaultdict(lambda: [0, 0])
        times, wrong = [], []
        for r in rows:
            f = data / kind / (r["file"] if kind == "crops" else r["file"].replace(".png", ".jpg"))
            img = cv2.imread(str(f))
            t = time.perf_counter()
            if kind == "crops":
                reads = [reader.ocr.read(img)]
            else:
                reads = reader.read_frame(img)
            times.append((time.perf_counter() - t) * 1000)
            got = next((x.plate for x in reads if x.plate), None)
            ok = got == r["truth"]
            for key in ("all", r["script"], f"{r['script']}/{r['condition']}"):
                tally[key][0] += ok
                tally[key][1] += 1
            if not ok:
                wrong.append({"file": r["file"], "truth": r["truth"], "got": got,
                              "raw": [x.raw for x in reads]})
        times.sort()
        out[kind] = {
            "accuracy_pct": {k: _pct(*v) for k, v in sorted(tally.items())},
            "n": {k: v[1] for k, v in sorted(tally.items())},
            "ms_per_image": {"mean": round(sum(times) / len(times), 1), "p50": round(times[len(times) // 2], 1),
                             "p95": round(times[int(len(times) * 0.95)], 1)},
            "wrong": wrong,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="edge/tests/data/plates")
    ap.add_argument("--ocr", nargs="+", default=["fast", "auto"], choices=["fast", "auto", "easyocr"])
    ap.add_argument("--out", default="edge/tests/ocr_report.json")
    a = ap.parse_args()
    report = {"cpu": platform.processor(), "machine": platform.machine(), "results": []}
    for mode in a.ocr:
        res = evaluate(Path(a.data), mode)
        report["results"].append(res)
        for kind in ("crops", "scenes"):
            acc = res[kind]["accuracy_pct"]
            print(f"{mode:8s} {kind:6s} all {acc['all']:5.1f}%  latin {acc['latin']:5.1f}%  arabic {acc['arabic']:5.1f}%"
                  f"  {res[kind]['ms_per_image']['mean']:.0f} ms/img (p95 {res[kind]['ms_per_image']['p95']:.0f})")
    Path(a.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
