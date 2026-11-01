"""End-to-end dry runs on the synthetic set. Needs `uv sync --group edge` and the models (downloaded on first
use, ~10 MB for the plate path); skipped otherwise. EasyOCR is not used here (slow to load)."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytest.importorskip("fast_alpr")

from edge.camera import plate_reader, spot_watcher  # noqa: E402
from edge.camera.common import validate_signal  # noqa: E402
from edge.tests import synth  # noqa: E402


@pytest.fixture(scope="module")
def data(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("edge")
    synth.make_plate_set(out / "plates", seed=7, per_cond=1)
    synth.make_gate_video(out / "gate.mp4")
    synth.make_overhead_lot(out / "overhead")
    return out


def _run(main, argv) -> tuple[dict, list[dict]]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        stats = main(argv)
    sigs = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    for s in sigs:
        validate_signal(s)
    return stats, sigs


def test_plate_reader_video_sends_one_enter(data):
    stats, sigs = _run(plate_reader.main, ["--source", str(data / "gate.mp4"), "--lot-id", "AUB", "--ocr", "fast",
                                           "--clock", "field", "--dry-run"])
    assert [s["payload"]["plate_norm"] for s in sigs] == ["B123456"]
    assert sigs[0]["source"] == "camera_plate" and sigs[0]["kind"] == "enter"
    assert sigs[0]["device_id"] == "cam-entrance-1"


def test_plate_reader_image_folder_exit(data):
    # Each scene is a different car shown once: nothing reaches 2-of-5, so no signal (debounce works).
    stats, sigs = _run(plate_reader.main, ["--source", str(data / "plates" / "scenes"), "--lot-id", "AUB",
                                           "--ocr", "fast", "--kind", "exit", "--clock", "field", "--dry-run"])
    assert sigs == [] and stats["per_frame"]["frames"] == 10


def test_spot_watcher_diff_sequence(data):
    ov = data / "overhead"
    stats, sigs = _run(spot_watcher.main, ["--source", str(ov / "seq"), "--spots", str(ov / "spots.json"),
                                           "--detector", "diff", "--empty", str(ov / "empty.jpg"),
                                           "--clock", "field", "--dry-run"])
    got = [(s["payload"]["space_id"], s["kind"]) for s in sigs]
    # occupancy: empty -> [1,0,0,1] -> [1,1,0,1] -> [0,1,0,1]
    assert got == [("LAU-01", "occupied"), ("LAU-04", "occupied"), ("LAU-02", "occupied"), ("LAU-01", "vacant")]
    assert stats["state"] == {"LAU-01": "vacant", "LAU-02": "occupied", "LAU-03": "vacant", "LAU-04": "occupied"}
