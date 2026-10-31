"""Signing helpers for self-contained booking and rotating gate QR codes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api import db

ROOT = Path(__file__).resolve().parents[2]
KEYS_DIR = Path(os.environ.get("GOPARK_KEYS_DIR", ROOT / "keys"))
PRIVATE_KEY_PATH = KEYS_DIR / "qr-ed25519-private.pem"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def private_key() -> Ed25519PrivateKey:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY_PATH.exists():
        data = PRIVATE_KEY_PATH.read_bytes()
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError("Stored QR key is not an Ed25519 private key")
        return key

    generated = Ed25519PrivateKey.generate()
    pem = generated.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    try:
        fd = os.open(PRIVATE_KEY_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return private_key()
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return generated


def public_key_b64() -> str:
    raw = private_key().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64url(raw)


def sign_booking(booking) -> str:
    plate = booking["plate_norm"] or ""
    payload = {
        "bid": booking["id"],
        "lot": booking["lot_id"],
        "ph": hashlib.sha256(plate.encode("utf-8")).hexdigest()[:16],
        "s": db.local(booking["start_at"]),
        "e": db.local(booking["end_at"]),
        "n": secrets.token_urlsafe(6)[:8],
    }
    encoded = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    unsigned = f"GP1.{encoded}"
    signature = private_key().sign(unsigned.encode("ascii"))
    return f"{unsigned}.{_b64url(signature)}"


def verify_booking_qr(value: str) -> dict | None:
    try:
        prefix, encoded, signature = value.split(".")
        if prefix != "GP1":
            return None
        unsigned = f"{prefix}.{encoded}"
        public_key = private_key().public_key()
        public_key.verify(_unb64url(signature), unsigned.encode("ascii"))
        payload = json.loads(_unb64url(encoded))
        if set(payload) != {"bid", "lot", "ph", "s", "e", "n"}:
            return None
        if not all(isinstance(payload[k], str) and payload[k] for k in ("bid", "lot", "ph", "s", "e", "n")):
            return None
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, InvalidSignature):
        return None


def gate_code(lot_id: str, secret: str, at: datetime) -> tuple[str, datetime]:
    window = int(at.timestamp()) // 30
    digest = hmac.new(secret.encode("utf-8"), str(window).encode("ascii"), hashlib.sha256).hexdigest()[:8]
    valid_until = datetime.fromtimestamp((window + 1) * 30, at.tzinfo)
    return f"GPG1.{lot_id}.{digest}", valid_until


def verify_gate_code(value: str, lot_id: str, secret: str, at: datetime) -> bool:
    try:
        prefix, code_lot, supplied = value.split(".")
    except ValueError:
        return False
    if prefix != "GPG1" or code_lot != lot_id or len(supplied) != 8:
        return False
    current = int(at.timestamp()) // 30
    for window in (current - 1, current, current + 1):
        expected = hmac.new(secret.encode("utf-8"), str(window).encode("ascii"), hashlib.sha256).hexdigest()[:8]
        if hmac.compare_digest(supplied, expected):
            return True
    return False
