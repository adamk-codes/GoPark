from __future__ import annotations

import numpy as np
import pytest

from edge.camera.vehicle_matcher import VehicleMatcher, vehicle_prompt


class StubModel:
    def __init__(self, scores):
        self.scores = scores

    def similarities(self, image_bgr, prompts):
        return self.scores


def expected(booking_id, color, body, make):
    return {"booking_id": booking_id, "user_id": f"U-{booking_id}", "p": 0.6,
            "vehicle": {"color": color, "body": body, "make": make, "model": None}}


def test_matcher_ranks_profiles_and_limits_payload_candidates():
    rows = [expected(f"B{i}", "white", "sedan", "Kia") for i in range(12)]
    model = StubModel([0.4, 0.5, 0.9, 0.7, 0.6, 0.1, 0.2, 0.3, 0.4, 0.5, 0.3, 0.6])
    result = VehicleMatcher(model, model_name="stub").match(np.ones((8, 8, 3), dtype=np.uint8), rows)
    assert result is not None
    assert len(result.candidates) == 10
    assert result.candidates[0]["booking_id"] == "B2"
    assert result.top_margin == pytest.approx(0.2)
    assert vehicle_prompt(rows[0]["vehicle"]) == "a photo of a white Kia sedan car"


def test_matcher_returns_no_match_without_image_or_expected_candidates():
    matcher = VehicleMatcher(StubModel([]))
    assert matcher.match(np.empty((0, 0, 3), dtype=np.uint8), []) is None


def test_matcher_rejects_mismatched_model_output_count():
    matcher = VehicleMatcher(StubModel([0.9]))
    with pytest.raises(ValueError, match="one score per expected vehicle"):
        matcher.match(np.ones((8, 8, 3), dtype=np.uint8), [expected("B1", "blue", "suv", "BMW"),
                                                            expected("B2", "white", "sedan", "Kia")])
