"""Fusion accuracy: compare what fusion decided with the traces' ground truth (CONTRACT.md §7, fusion_metrics.json).

Per booking the truth is `arrives` / `actual_arrival` / `actual_departure` from traces/index.csv,
and each signal's `truth` flag. Decisions come either from a full replay through the API
(`replay.replay`, the numbers we report) or from `emulate`, which runs the same pure
`fusion.decide` per booking without a database; tuning uses `emulate` because it is ~100x faster.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from tools.simulator.timeline import Trace, parse, send_time

EARLY_MIN = 10   # a confirm/checkout this long before the real event counts as wrong
OBJECTIVE = ("auto_confirm_rate - 10*false_confirm_rate - 5*early_confirm_rate "
             "+ 0.5*auto_checkout_rate - 5*early_checkout_rate - 0.005*median_time_to_confirm_min; "
             "tuning also subtracts 0.05*sum|ln(log_lr / starting log_lr)|")


def split_of(booking_id: str) -> str:
    """Stable half split: 'tune' or 'test'."""
    return "tune" if hashlib.sha1(booking_id.encode()).digest()[0] % 2 == 0 else "test"


# ---------- offline emulation (pure fusion.decide) ----------
def _signals(trace: Trace) -> list[tuple[datetime, dict]]:
    """(send time, fusion signal) for every line the API would attribute to this booking."""
    out = []
    for i, line in enumerate(trace.lines):
        payload = line.get("payload") or {}
        fuzzy = False
        if line["source"] == "camera_plate":
            plate = (payload.get("plate_norm") or "").upper()
            if plate != trace.plate_norm:
                close = len(plate) == len(trace.plate_norm or "") and sum(
                    a != b for a, b in zip(plate, trace.plate_norm)) == 1
                if not close or float(payload.get("ocr_conf", 1)) >= 0.8:
                    continue
                fuzzy = True
        out.append((send_time(line), {"id": i, "source": line["source"], "kind": line["kind"],
                                      "lot_id": line["lot_id"], "observed_at": parse(line["observed_at"]),
                                      "elsewhere": line["lot_id"] != trace.lot_id, "fuzzy": fuzzy,
                                      "truth": bool(line.get("truth"))}))
    # A gate_sensor event is evidence only when paired with one of those plate reads (same direction, within window).
    window = timedelta(seconds=8)
    reads = [s for _, s in out if s["source"] == "camera_plate"]
    out = [(t, s) for t, s in out if s["source"] != "gate_sensor" or any(
        r["kind"] == s["kind"] and abs(r["observed_at"] - s["observed_at"]) <= window for r in reads)]
    out.sort(key=lambda item: item[0])
    return out


def emulate(trace: Trace, cfg: dict, priors: list[dict]) -> dict:
    """Decision record for one trace, as `replay.record` would give, without the API."""
    from api.ops import fusion
    stay = None
    rec = {"arriving_at": None, "confirmed_at": None, "checked_out_at": None, "check_out_action": None,
           "in_at": None, "out_at": None, "in_method": None, "alerts": []}
    signals = _signals(trace)
    times = sorted({at for at, _ in signals})
    infer_at = trace.end + timedelta(minutes=30)
    received: list[dict] = []
    for at in times + [None]:
        if stay and stay["state"] in fusion.OPEN and (at is None or at > infer_at):
            # Silent departure, as refresh_alerts + fusion.inferred_departure do it.
            exits = [s["observed_at"] for s in received if s["kind"] in fusion.DEPARTURE_POSITIVE
                     and not s["elsewhere"] and stay["in_at"] < s["observed_at"] <= infer_at]
            stay = {**stay, "state": "CHECKED_OUT", "inferred": True}
            rec.update(out_at=exits[-1] if exits else trace.end, checked_out_at=infer_at,
                       check_out_action="inferred_check_out")
        if at is None:
            break
        received += [s for t, s in signals if t == at]
        prior = fusion.prior_for(trace.user_id, trace.lot_id, at, priors, cfg)
        d = fusion.decide(stay, received, at, prior, cfg)
        if d["arrival"]:
            stay = {"state": d["state"], "in_at": d["in_at"], "inferred": False}
            rec["in_at"], rec["in_method"] = d["in_at"], "auto"
            rec["arriving_at"] = rec["arriving_at"] or at
            if d["arrival"] == "check_in":
                rec["confirmed_at"] = at
        if d["contradiction"] and "anomaly" not in rec["alerts"]:
            rec["alerts"].append("anomaly")
        if d["departure"]:
            stay = {**stay, "state": "CHECKED_OUT", "inferred": d["departure"] == "moved"}
            rec["out_at"] = d["out_at"]
            if d["departure"] != "correct_out":
                rec["checked_out_at"] = at
                rec["check_out_action"] = {"check_out": "auto_check_out",
                                           "moved": "closed_by_arrival_elsewhere"}[d["departure"]]
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"booking_id": trace.booking_id, "lot_id": trace.lot_id, "lot_kind": trace.lot_kind,
            "scenario": trace.scenario, "variant": trace.variant, "arrives": trace.arrives,
            "actual_arrival": iso(trace.actual_arrival), "actual_departure": iso(trace.actual_departure),
            "start": iso(trace.start), "end": iso(trace.end), "state": stay["state"] if stay else None,
            **{k: iso(v) if isinstance(v, datetime) else v for k, v in rec.items()}}


def false_evidence_confirm(trace: Trace, rec: dict, cfg: dict, priors: list[dict]) -> bool:
    """Auto-confirmed, but the signals with truth=true alone would not have confirmed it then."""
    from api.ops import fusion
    if not rec.get("confirmed_at") or rec.get("in_method") != "auto":
        return False
    at = parse(rec["confirmed_at"])
    true_only = [s for t, s in _signals(trace) if t <= at and s["truth"]]
    prior = fusion.prior_for(trace.user_id, trace.lot_id, at, priors, cfg)
    return fusion.decide(None, true_only, at, prior, cfg)["state"] != "CHECKED_IN"


# ---------- metrics ----------
def _minutes(a: str | None, b: str | None) -> float | None:
    return None if not a or not b else (parse(a) - parse(b)).total_seconds() / 60


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def _p90(values: list[float]) -> float | None:
    return round(sorted(values)[int(0.9 * (len(values) - 1))], 2) if values else None


def metrics(records: list[dict]) -> dict:
    """Rates over arrivals (cars that came) and no-shows; departures over cars that came and got a stay."""
    arrivals = [r for r in records if r["arrives"]]
    no_shows = [r for r in records if not r["arrives"]]
    auto = [r for r in arrivals if r["confirmed_at"] and r["in_method"] == "auto"]
    asked = [r for r in arrivals if r not in auto and r["arriving_at"]]
    missed = [r for r in arrivals if not r["confirmed_at"] and not r["arriving_at"]]
    ttc = [_minutes(r["confirmed_at"], r["actual_arrival"]) for r in auto]
    early = [m for m in ttc if m < -EARLY_MIN]
    seen = [r for r in arrivals if r["in_at"]]
    out_auto = [r for r in seen if r["check_out_action"] == "auto_check_out"]
    out_moved = [r for r in seen if r["check_out_action"] == "closed_by_arrival_elsewhere"]
    out_inferred = [r for r in seen if r["check_out_action"] == "inferred_check_out"]
    out_open = [r for r in seen if not r["check_out_action"]]
    ttco = [_minutes(r["checked_out_at"], r["actual_departure"]) for r in out_auto]
    errors = [abs(_minutes(r["out_at"], r["actual_departure"])) for r in seen if r["out_at"]]
    alerts = lambda kind, rows: sum(kind in r["alerts"] for r in rows)  # noqa: E731
    out = {
        "bookings": len(records), "arrivals": len(arrivals), "no_shows": len(no_shows),
        "auto_confirm_rate": _rate(len(auto), len(arrivals)),
        "ask_driver_rate": _rate(len(asked), len(arrivals)),
        "missed_arrival_rate": _rate(len(missed), len(arrivals)),
        "false_confirm_rate": _rate(sum(1 for r in no_shows if r["confirmed_at"]), len(no_shows)),
        "false_ask_rate": _rate(sum(1 for r in no_shows if r["arriving_at"] and not r["confirmed_at"]), len(no_shows)),
        "early_confirm_rate": _rate(len(early), len(auto)),
        "median_time_to_confirm_min": _median(ttc), "p90_time_to_confirm_min": _p90(ttc),
        "low_confidence_alerts": alerts("low_confidence", records), "anomaly_alerts": alerts("anomaly", records),
        "departure": {
            "stays": len(seen),
            "auto_checkout_rate": _rate(len(out_auto), len(seen)),
            "moved_checkout_rate": _rate(len(out_moved), len(seen)),
            "inferred_checkout_rate": _rate(len(out_inferred), len(seen)),
            "still_open_rate": _rate(len(out_open), len(seen)),
            "early_checkout_rate": _rate(sum(1 for m in ttco if m < -EARLY_MIN), len(out_auto)),
            "median_time_to_checkout_min": _median(ttco),
            "median_out_at_error_min": _median(errors), "p90_out_at_error_min": _p90(errors),
        },
    }
    if "false_evidence_confirm" in (records[0] if records else {}):
        out["false_evidence_confirms"] = sum(1 for r in records if r["false_evidence_confirm"])
    return out


def objective(m: dict) -> float:
    def v(x):
        return x or 0.0
    return round(v(m["auto_confirm_rate"]) - 10 * v(m["false_confirm_rate"]) - 5 * v(m["early_confirm_rate"])
                 + 0.5 * v(m["departure"]["auto_checkout_rate"]) - 5 * v(m["departure"]["early_checkout_rate"])
                 - 0.005 * max(0.0, v(m["median_time_to_confirm_min"])), 4)


def report(records: list[dict]) -> dict:
    groups = lambda key: {k: metrics([r for r in records if r[key] == k])  # noqa: E731
                          for k in sorted({r[key] for r in records})}
    return {"all": metrics(records),
            "tune": metrics([r for r in records if split_of(r["booking_id"]) == "tune"]),
            "test": metrics([r for r in records if split_of(r["booking_id"]) == "test"]),
            "by_scenario": groups("scenario"), "by_lot_kind": groups("lot_kind")}


# ---------- tuning ----------
MULTIPLIERS = [0.5, 0.75, 1.25, 1.5, 2.0]
SEARCH = {
    "signals.geofence.log_lr": MULTIPLIERS, "signals.motion.log_lr": MULTIPLIERS,
    "signals.ble.log_lr": MULTIPLIERS, "signals.camera_plate.log_lr": MULTIPLIERS,
    "signals.camera_plate.fuzzy_log_lr": MULTIPLIERS, "signals.camera_spot.log_lr": MULTIPLIERS,
    "freshness_half_life_min": [5, 10, 15, 30, 60, 120, 240],
    "default_prior": [0.2, 0.3, 0.4, 0.5], "departure_prior": [0.2, 0.3, 0.4, 0.5],
    "max_prior": [0.7, 0.8, 0.9, 0.99], "min_evidence_log_lr": [0.0, 1.0, 2.0, 3.0, 4.0],
    "elsewhere_factor": [0.0, 0.5, 1.0, 2.0],
}


def _get(cfg: dict, path: str):
    for key in path.split("."):
        cfg = cfg[key]
    return cfg


def _set(cfg: dict, path: str, value) -> None:
    *head, last = path.split(".")
    for key in head:
        cfg = cfg[key]
    cfg[last] = value


# The generator never emits a false motion/BLE/camera signal, so unchecked tuning inflates those weights.
# Each log-LR pays SHRINK per unit of |ln(new / start)|: it moves only when that buys real accuracy.
SHRINK = 0.05


def tune(traces: list[Trace], cfg: dict, priors: list[dict], rounds: int = 2, log=print) -> tuple[dict, list[dict]]:
    """Coordinate search over SEARCH on the tune split (offline emulation); returns (config, steps)."""
    tune_set = [t for t in traces if split_of(t.booking_id) == "tune"]
    base = {path: _get(cfg, path) for path in SEARCH}

    def score(c: dict) -> float:
        drift = sum(abs(math.log(_get(c, p) / base[p])) for p, o in SEARCH.items() if o is MULTIPLIERS)
        return round(objective(metrics([emulate(t, c, priors) for t in tune_set])) - SHRINK * drift, 4)
    best_cfg, best = copy.deepcopy(cfg), score(cfg)
    steps = [{"step": "start", "objective": best}]
    log(f"tune: start objective {best} on {len(tune_set)} bookings")
    for _ in range(rounds):
        improved = False
        for path, options in SEARCH.items():
            values = [round(base[path] * m, 3) for m in options] if options is MULTIPLIERS else options
            for value in values:
                if value == _get(best_cfg, path):
                    continue
                candidate = copy.deepcopy(best_cfg)
                _set(candidate, path, value)
                s = score(candidate)
                if s > best + 1e-4:
                    best, best_cfg, improved = s, candidate, True
                    steps.append({"step": path, "value": value, "objective": s})
                    log(f"tune: {path} = {value} -> {s}")
        if not improved:
            break
    return best_cfg, steps


def changed_paths(before: dict, after: dict) -> dict:
    return {p: [_get(before, p), _get(after, p)] for p in SEARCH if _get(before, p) != _get(after, p)}


def write_config(path: Path, changes: dict) -> None:
    """Rewrite tuned values in config/fusion.yaml in place, keeping its comments and layout."""
    text = path.read_text(encoding="utf-8")
    for dotted, (_, new) in changes.items():
        parts = dotted.split(".")
        if parts[0] == "signals":
            pattern = rf"(^\s+{parts[1]}:\s*\{{[^}}\n]*?\b{parts[2]}:\s*)[-0-9.]+"
        else:
            pattern = rf"(^{parts[0]}:\s*)[-0-9.]+"
        text, n = re.subn(pattern, rf"\g<1>{new}", text, count=1, flags=re.M)
        if n != 1:
            raise ValueError(f"could not place {dotted} in {path}")
    path.write_text(text, encoding="utf-8")
