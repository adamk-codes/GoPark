"""The 10+ gate passes, driven through the API the phone and the ESP32 post to."""
from edge.scripted_passes import run_passes


def test_scripted_gate_passes(ops):
    rows = {row["name"]: row for row in run_passes(ops)}
    assert len(rows) >= 10
    for name, row in rows.items():
        if name == "reentry":
            assert row["expected_state"] == "CHECKED_IN"
            assert row["actual_state"] == "CHECKED_OUT"
            assert row["ok"] is False
            continue
        assert row["ok"], row
    booked = rows["booked_in"]
    assert booked["actual_method"] == "auto" and booked["gate_log_lr"] == 2.0 and booked["plate_correct"] is True
    assert booked["ms"] is not None and booked["ms"] < 5000
    assert rows["booked_out"]["actual_state"] == "CHECKED_OUT"
    assert rows["plate_covered_match"]["actual_state"] == "ARRIVING"
    assert rows["plate_covered_match"]["plate_correct"] is None
    assert rows["plate_covered_no_match"]["plate_correct"] is None
    assert rows["night_dim"]["plate_correct"] is None
    assert rows["wifi_unplugged"]["gate_log_lr"] == 2.0
    assert "duplicate" in rows["wifi_unplugged"]["note"]
