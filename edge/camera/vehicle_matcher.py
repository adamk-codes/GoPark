"""Suggest expected vehicles from an in-memory car crop using zero-shot CLIP.

This never identifies a booking from an image with certainty and its signal is capped below auto-confirm.
The optional edge dependency group provides Transformers/PyTorch; tests use injected similarity scores.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

import numpy as np


class VehicleProfile(TypedDict):
    color: str
    body: str
    make: str
    model: str | None


class ExpectedVehicle(TypedDict):
    booking_id: str
    user_id: str
    vehicle: VehicleProfile
    p: float


class SimilarityModel(Protocol):
    def similarities(self, image_bgr: np.ndarray, prompts: list[str]) -> list[float]: ...


def vehicle_prompt(vehicle: VehicleProfile | dict) -> str:
    color = vehicle["color"]
    body = vehicle["body"]
    make = vehicle["make"]
    return f"a photo of a {color} {make} {body} car"


@dataclass
class Match:
    candidates: list[dict]
    top_margin: float

    def as_payload(self, model: str | None = None) -> dict:
        payload = {"candidates": self.candidates, "top_margin": round(self.top_margin, 6)}
        if model:
            payload["model"] = model
        return payload


class HuggingFaceCLIP:
    """CPU/GPU-capable Hugging Face CLIP adapter. Frames stay in memory and are never written."""

    def __init__(self, model_name: str = "openai/clip-vit-base-patch32", device: str | None = None):
        import torch
        from transformers import CLIPModel, CLIPProcessor

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.processor = CLIPProcessor.from_pretrained(model_name)
        self.model = CLIPModel.from_pretrained(model_name).to(self.device).eval()
        self.model_name = model_name

    def similarities(self, image_bgr: np.ndarray, prompts: list[str]) -> list[float]:
        import cv2
        from PIL import Image

        image = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
        batch = self.processor(text=prompts, images=image, return_tensors="pt", padding=True)
        batch = {key: value.to(self.device) for key, value in batch.items()}
        with self.torch.inference_mode():
            outputs = self.model(**batch)
            image_features = outputs.image_embeds / outputs.image_embeds.norm(dim=-1, keepdim=True)
            text_features = outputs.text_embeds / outputs.text_embeds.norm(dim=-1, keepdim=True)
            cosine = (image_features @ text_features.T).squeeze(0)
        # Cosine is a ranking score, not a calibrated probability. Map only to a bounded 0–1 score.
        return [float((value.item() + 1) / 2) for value in cosine]


class VehicleMatcher:
    def __init__(self, model: SimilarityModel, *, model_name: str | None = None, limit: int = 10,
                 single_candidate_baseline: float = 0.15):
        self.model = model
        self.model_name = model_name
        self.limit = max(1, min(limit, 10))
        self.single_candidate_baseline = single_candidate_baseline

    def match(self, image_bgr: np.ndarray, expected: list[ExpectedVehicle]) -> Match | None:
        if image_bgr.size == 0 or not expected:
            return None
        candidates = expected
        scores = self.model.similarities(image_bgr, [vehicle_prompt(row["vehicle"]) for row in candidates])
        if len(scores) != len(candidates):
            raise ValueError("vehicle model must return one score per expected vehicle")
        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)[:self.limit]
        top_margin = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else max(
            0.0, ranked[0][1] - self.single_candidate_baseline)
        return Match(
            candidates=[{"booking_id": row["booking_id"], "score": round(max(0.0, min(1.0, score)), 6),
                         "vehicle": dict(row["vehicle"])} for row, score in ranked],
            top_margin=max(0.0, min(1.0, top_margin)),
        )


def load_matcher(model_name: str = "openai/clip-vit-base-patch32", device: str | None = None) -> VehicleMatcher:
    model = HuggingFaceCLIP(model_name, device)
    return VehicleMatcher(model, model_name=model_name)
