"""Evaluate car-look match on a licensed, labeled image set supplied by the user.

Manifest CSV columns: image,truth_booking_id,truth_color,truth_body,expected_vehicles. The final field is JSON
containing the expected_vehicles endpoint response for that frame. Images are read in memory and never saved.

Example: uv run --group edge python -m edge.tests.eval_vehicle_matcher --manifest edge/tests/data/vehicle_eval.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from edge.camera.vehicle_matcher import load_matcher


def evaluate(manifest: Path, model_name: str, output: Path | None = None) -> dict:
    import cv2

    matcher = load_matcher(model_name)
    groups: dict[int, dict[str, int]] = defaultdict(lambda: {"n": 0, "color": 0, "body": 0, "match": 0})
    with manifest.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            image_path = Path(row["image"])
            if not image_path.is_absolute():
                image_path = manifest.parent / image_path
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"cannot read labeled image: {image_path}")
            expected = json.loads(row["expected_vehicles"])
            result = matcher.match(image, expected)
            if result is None:
                continue
            count = len(expected)
            metrics = groups[count]
            metrics["n"] += 1
            top = result.candidates[0]
            metrics["color"] += top["vehicle"]["color"] == row["truth_color"]
            metrics["body"] += top["vehicle"]["body"] == row["truth_body"]
            metrics["match"] += top["booking_id"] == row["truth_booking_id"]
    report = {
        "model": model_name,
        "manifest": str(manifest),
        "note": "Results apply only to this licensed labeled set and are not calibrated field accuracy.",
        "by_expected_count": {
            str(count): {"images": m["n"], "top1_color": m["color"] / m["n"],
                         "top1_body": m["body"] / m["n"], "top1_match": m["match"] / m["n"]}
            for count, m in sorted(groups.items()) if m["n"]
        },
    }
    if output:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--model", default="openai/clip-vit-base-patch32")
    ap.add_argument("--out", type=Path, help="optional JSON report path")
    args = ap.parse_args(argv)
    report = evaluate(args.manifest, args.model, args.out)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
