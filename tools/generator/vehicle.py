"""Deterministic visual vehicle profiles for synthetic Lebanese booking data."""
from __future__ import annotations

import random

COLORS = ("white", "silver", "black", "grey", "blue", "red", "green", "beige")
BODIES = ("sedan", "hatchback", "suv", "pickup", "van")
MAKES = ("Kia", "Toyota", "Hyundai", "Nissan", "Mercedes", "BMW")


def make_vehicle_profile(rng: random.Random) -> dict[str, str | None]:
    """Return a Lebanon-weighted vehicle profile; visual labels are synthetic, not a population survey."""
    return {
        "color": rng.choices(COLORS, weights=(31, 24, 22, 10, 6, 3, 1, 3), k=1)[0],
        "body": rng.choices(BODIES, weights=(39, 15, 35, 5, 6), k=1)[0],
        "make": rng.choices(MAKES, weights=(22, 20, 20, 14, 13, 11), k=1)[0],
        "model": None,
    }
