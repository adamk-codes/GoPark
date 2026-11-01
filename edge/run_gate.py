"""One-command gate: the phone stream plus the plate reader flags in config/gates.yaml.

    uv run --group edge python -m edge.run_gate --lot-id AUB --phone http://PHONE_IP:8080

Ctrl+C stops the reader and flushes its queue, including on Windows. `--clock field` when the
ESP32 is using NTP; the yaml default is `demo`.
"""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "gates.yaml"


def load_gates(path: Path | None = None) -> dict:
    path = path or DEFAULT_CONFIG
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("lots"), dict):
        raise SystemExit(f"{path} needs a lots: map")
    raw["defaults"] = raw.get("defaults") or {}
    raw["_path"] = str(path)
    return raw


def lot_settings(cfg: dict, lot_id: str) -> dict:
    lots = cfg.get("lots") or {}
    if lot_id not in lots:
        known = ", ".join(sorted(lots)) or "(none)"
        raise SystemExit(f"unknown lot {lot_id}; known lots: {known}")
    merged = {**(cfg.get("defaults") or {}), **(lots[lot_id] or {})}
    merged["lot_id"] = lot_id
    return merged


def stream_url(phone: str) -> str:
    """IP Webcam shows http://<ip>:8080. The reader wants the /video stream."""
    base = phone.strip()
    if not base:
        raise SystemExit("phone URL is empty")
    if "://" not in base:
        return base
    path = base.split("?", 1)[0].rstrip("/")
    if path.endswith("/video"):
        return base
    return path + "/video"


def _num(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def reader_argv(settings: dict, *, source: str, api: str | None = None, clock: str | None = None,
                roi=None, fps=None, min_conf=None, need=None, dry_run: bool = False) -> list[str]:
    """argv for `edge.camera.plate_reader`. Agreed flags: auto, api, cars, vehicle-match."""
    kind = settings.get("kind") or "auto"
    trigger = settings.get("trigger") or "api"
    if kind == "auto" and trigger not in ("listen", "api"):
        raise SystemExit("--kind auto needs trigger api or listen")
    gate = int(settings.get("gate") or 1)
    lane = int(settings.get("lane") or 1)
    lot = settings["lot_id"]
    box = settings.get("roi") if roi is None else roi
    argv = [
        "--source", source,
        "--lot-id", lot,
        "--device-id", str(settings.get("device_id") or "cam-gate-1"),
        "--kind", kind,
        "--trigger", trigger,
        "--api", api or str(settings.get("api") or "http://localhost:8000"),
        "--clock", clock or str(settings.get("clock") or "demo"),
        "--ocr", str(settings.get("ocr") or "auto"),
        "--fps", _num(settings.get("fps") if fps is None else fps),
        "--min-conf", _num(settings.get("min_conf") if min_conf is None else min_conf),
        "--need", _num(settings.get("need") if need is None else need),
        "--window", _num(settings.get("window") or 5),
        "--cooldown", _num(settings.get("cooldown_s") or 120),
        "--window-s", _num(settings.get("window_s") or 8),
        "--gate-id", f"{lot}-{gate}",
        "--lane", str(lane),
    ]
    if settings.get("cars", True):
        argv.append("--cars")
    if settings.get("vehicle_match", True):
        argv += ["--vehicle-match", "--clip-model", str(settings.get("clip_model") or "openai/clip-vit-base-patch32")]
    if box:
        if len(box) != 4:
            raise SystemExit("roi must be x,y,w,h")
        argv += ["--roi", ",".join(str(int(v)) for v in box)]
    if dry_run:
        argv.append("--dry-run")
    return argv


def ctrl_c_should_stop(ctrl_type: int) -> bool:
    """Windows console: Ctrl+C is 0, Ctrl+Break is 1. Close / logoff / shutdown are not swallowed."""
    return ctrl_type in (0, 1)


_CTRL_HANDLER = None


def install_windows_ctrl_c() -> None:
    """Ask the main thread to raise KeyboardInterrupt so the plate reader can flush its queue."""
    global _CTRL_HANDLER
    if sys.platform != "win32":
        return
    import ctypes
    ctypes.pythonapi.PyErr_SetInterrupt.argtypes = []
    ctypes.pythonapi.PyErr_SetInterrupt.restype = None

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def _on_ctrl(ctrl_type):
        if ctrl_c_should_stop(int(ctrl_type)):
            ctypes.pythonapi.PyErr_SetInterrupt()
            return 1
        return 0

    if ctypes.windll.kernel32.SetConsoleCtrlHandler(_on_ctrl, 1):
        _CTRL_HANDLER = _on_ctrl


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lot-id", required=True)
    ap.add_argument("--phone", help="IP Webcam base URL, http://<phone>:8080 (the /video path is added)")
    ap.add_argument("--source", help="use this stream or file instead of --phone (a clip, for the demo fallback)")
    ap.add_argument("--api")
    ap.add_argument("--clock", choices=["demo", "field"])
    ap.add_argument("--roi", help="x,y,w,h override")
    ap.add_argument("--fps", type=float)
    ap.add_argument("--min-conf", type=float)
    ap.add_argument("--need", type=int)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-only", action="store_true", help="print the plate_reader arguments and exit")
    args = ap.parse_args(argv)

    settings = lot_settings(load_gates(args.config), args.lot_id)
    if args.source:
        source = args.source
    elif args.phone or settings.get("phone"):
        source = stream_url(args.phone or settings["phone"])
    else:
        raise SystemExit(f"pass --phone http://<phone-ip>:8080 for lot {args.lot_id}")
    roi = [int(v) for v in args.roi.split(",")] if args.roi else None
    reader_args = reader_argv(settings, source=source, api=args.api, clock=args.clock, roi=roi,
                              fps=args.fps, min_conf=args.min_conf, need=args.need, dry_run=args.dry_run)
    if args.print_only:
        print(" ".join(reader_args))
        return reader_args

    signal.signal(signal.SIGINT, signal.default_int_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    install_windows_ctrl_c()
    try:
        from edge.camera.plate_reader import main as reader_main
    except ImportError as exc:
        raise SystemExit("plate reader needs the edge group: uv sync --group edge\n" + str(exc)) from exc
    try:
        return reader_main(reader_args)
    except KeyboardInterrupt:
        return None


if __name__ == "__main__":
    main()
