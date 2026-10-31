"""Time-based backtest of the engine against baselines B1–B3 (ARCHITECTURE_PLAN.md §4.4).

This is the **only** module that may read `truth.json` (pattern recovery). Everything else
(next-booking Hit@1, time MAE, week-ahead slot precision/recall) is scored on bookings alone.

Splits are counted back from the dataset's last finished week: the last `test_weeks` weeks are
the test split, the `validation_weeks` before them the validation split (tune only on that).
Each week of a split is one rolling cutoff: train on everything up to the Sunday before it,
predict that week.

    uv run python -m rhythm evaluate [--input data/bookings.csv] [--truth data/truth.json] [--run-id latest]
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from rhythm import baselines
from rhythm.io import load_bookings, load_calendar, load_config, resolve_run
from rhythm.pipeline import analyse, default_cutoff, history, run
from rhythm.stages.clean import normalise_status
from rhythm.stages.common import circ_diff
from rhythm.stages.predict import next_per_user

MODELS = ["engine", "B1", "B2", "B3"]
PRED_COLS = ["user_id", "day", "location_id", "minute"]


# ── splits and ground truth from bookings ────────────────────────────────────

def split_cutoffs(end: date, cfg: dict) -> dict[str, list[date]]:
    """Weekly cutoffs (each the last training day) per split, oldest first."""
    t, v = cfg["splits"]["test_weeks"], cfg["splits"]["validation_weeks"]
    return {"validation": [end - timedelta(weeks=k) for k in range(t + v, t, -1)],
            "test": [end - timedelta(weeks=k) for k in range(t, 0, -1)]}


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    local = df["start_time"].dt.tz_localize(None)
    return pd.DataFrame({"user_id": df["user_id"].to_numpy(), "day": local.dt.normalize().to_numpy(),
                         "location_id": df["location_id"].to_numpy(),
                         "minute": (local.dt.hour * 60 + local.dt.minute).to_numpy()})


def actual_bookings(df: pd.DataFrame, cutoff: date, window_days: int) -> pd.DataFrame:
    """Bookings the user kept (completed or no-show) in (cutoff, cutoff + window]."""
    d = df["start_time"].dt.date
    status = normalise_status(df["status"])
    sel = df[(d > cutoff) & (d <= cutoff + timedelta(days=window_days)) & status.isin(["completed", "no_show"])]
    return _frame(sel.sort_values("start_time"))


# ── predictions: each returns (top-1 next booking per user, predicted slots for the window) ──

def predict_engine(df, cutoff, cfg, calendar, window_days):
    an = analyse(df, cutoff, cfg, calendar)
    sl = an["slots"]
    top = next_per_user(sl)[PRED_COLS + ["p"]] if len(sl) else pd.DataFrame(columns=PRED_COLS + ["p"])
    week = sl[sl["is_habit"] & (sl["day"] <= pd.Timestamp(cutoff + timedelta(days=window_days)))]
    return top, week[PRED_COLS + ["p"]]


def predict_b1(df, cutoff, cfg, calendar, window_days):
    """B1: repeat last week."""
    d = df["start_time"].dt.date
    status = normalise_status(df["status"])
    last = df[(d > cutoff - timedelta(days=7)) & (d <= cutoff) & status.isin(["completed", "no_show"])]
    sl = _frame(last.sort_values("start_time"))
    sl["day"] = sl["day"] + pd.Timedelta(days=7)
    sl = sl[sl["day"] <= pd.Timestamp(cutoff + timedelta(days=window_days))].assign(p=1.0)
    return sl.groupby("user_id", as_index=False).first(), sl


def predict_b2(df, cutoff, cfg, calendar, window_days):
    """B2: modal weekdays / median time / modal lot over 8 weeks (`rhythm.baselines`)."""
    hist = history(df, cutoff)
    rows = [baselines.user_patterns(uid, ub, cutoff, cfg, "eval") for uid, ub in hist.groupby("user_id")]
    top, week = [], []
    for r in rows:
        if r["next_booking"] is not None:
            at = pd.Timestamp(r["next_booking"]["at"])
            top.append((r["user_id"], at.tz_localize(None).normalize(), r["location_id"], at.hour * 60 + at.minute, r["confidence"]))
        if r["pattern_type"] == "recurring":
            h, m = map(int, r["time_window"]["center"].split(":"))
            for k in range(1, window_days + 1):
                day = cutoff + timedelta(days=k)
                if baselines.DAY_NAMES[day.weekday()] in r["days"]:
                    week.append((r["user_id"], pd.Timestamp(day), r["location_id"], h * 60 + m, r["confidence"]))
    cols = PRED_COLS + ["p"]
    return pd.DataFrame(top, columns=cols), pd.DataFrame(week, columns=cols)


def predict_b3(df, cutoff, cfg, calendar, window_days, min_share: float = 0.5):
    """B3: frequency rules. Share of weeks with a booking at (lot, weekday) is the confidence;
    shares ≥ `min_share` are predicted, and the user's top-1 is the first of them (or, if none
    qualifies, the next date of their highest-share lot × weekday)."""
    hist = history(df, cutoff)
    hist = hist[normalise_status(hist["status"]).isin(["completed", "no_show"])]
    f = _frame(hist)
    f["weekday"] = f["day"].dt.weekday
    f["week"] = f["day"] - pd.to_timedelta(f["weekday"], unit="D")
    first_week = f.groupby("user_id")["week"].min()
    weeks = ((pd.Timestamp(cutoff) - first_week).dt.days // 7 + 1).rename("weeks")
    g = f.groupby(["user_id", "location_id", "weekday"]).agg(n=("week", "nunique"), minute=("minute", "median"))
    g = g.join(weeks, on="user_id").reset_index()
    g["p"] = (g["n"] / g["weeks"]).clip(upper=1.0)
    days = pd.DataFrame({"day": pd.to_datetime([cutoff + timedelta(days=k) for k in range(1, 8)])})
    days["weekday"] = days["day"].dt.weekday
    cand = g.merge(days, on="weekday")
    cols = PRED_COLS + ["p"]
    week = cand[(cand["p"] >= min_share) & (cand["day"] <= pd.Timestamp(cutoff + timedelta(days=window_days)))]
    strong = cand[cand["p"] >= min_share].sort_values(["user_id", "day", "p"], ascending=[True, True, False])
    top = strong.groupby("user_id").first().reset_index()
    rest = cand[~cand["user_id"].isin(top["user_id"])].sort_values(["user_id", "p", "day"], ascending=[True, False, True])
    top = pd.concat([top, rest.groupby("user_id").first().reset_index()])
    return top[cols], week[cols]


PREDICTORS = {"engine": predict_engine, "B1": predict_b1, "B2": predict_b2, "B3": predict_b3}


# ── scoring ───────────────────────────────────────────────────────────────────

def score_top1(pred: pd.DataFrame, actual: pd.DataFrame, tol: float) -> pd.DataFrame:
    """Per user with a real booking in the window: did the top-1 hit the *first* one?
    Hit = same day and lot, start within ±tol minutes. `time_err` only where day and lot match."""
    first = actual.groupby("user_id", as_index=False).first()
    m = first.merge(pred[PRED_COLS], on="user_id", how="left", suffixes=("", "_pred"))
    same = (m["day"] == m["day_pred"]) & (m["location_id"] == m["location_id_pred"])
    err = np.abs(circ_diff(m["minute"].to_numpy(float), m["minute_pred"].fillna(0).to_numpy(float)))
    m["time_err"] = np.where(same, err, np.nan)
    m["hit"] = same & (m["time_err"] <= tol)
    m["predicted"] = m["day_pred"].notna()
    return m[["user_id", "hit", "time_err", "predicted"]]


def score_slots(pred: pd.DataFrame, actual: pd.DataFrame, tol: float) -> dict:
    """Week-ahead slots: a predicted slot is correct if a real booking has the same user, day,
    lot and a start within ±tol; recall is the share of real bookings that were predicted."""
    p = pred[PRED_COLS].reset_index(drop=True).reset_index(names="pi")
    a = actual[PRED_COLS].reset_index(drop=True).reset_index(names="ai")
    m = p.merge(a, on=["user_id", "day", "location_id"], suffixes=("_p", "_a"))
    m = m[np.abs(circ_diff(m["minute_p"].to_numpy(float), m["minute_a"].to_numpy(float))) <= tol]
    return {"tp_pred": int(m["pi"].nunique()), "n_pred": len(p), "tp_actual": int(m["ai"].nunique()), "n_actual": len(a)}


def _summary(top: pd.DataFrame, slots: list[dict]) -> dict:
    tp_p, n_p = sum(s["tp_pred"] for s in slots), sum(s["n_pred"] for s in slots)
    tp_a, n_a = sum(s["tp_actual"] for s in slots), sum(s["n_actual"] for s in slots)
    prec, rec = (tp_p / n_p if n_p else 0.0), (tp_a / n_a if n_a else 0.0)
    return {"hit_at_1": round(float(top["hit"].mean()), 4) if len(top) else None,
            "time_mae_min": round(float(top["time_err"].mean()), 1) if top["time_err"].notna().any() else None,
            "coverage": round(float(top["predicted"].mean()), 4) if len(top) else None,
            "n": int(len(top)),
            "slot_precision": round(prec, 4), "slot_recall": round(rec, 4),
            "slot_f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0}


def backtest(df: pd.DataFrame, cfg: dict, calendar: dict | None, personas: dict[str, str] | None = None,
             models: list[str] | None = None, splits: list[str] | None = None) -> dict:
    """Hit@1 / time MAE / slot P-R per split × model (and per persona if given)."""
    models = models or MODELS
    tol, window = cfg["predict"]["hit_tolerance_minutes"], cfg["evaluate"]["window_days"]
    cuts = split_cutoffs(default_cutoff(df), cfg)
    out = {"cutoffs": {s: [c.isoformat() for c in cs] for s, cs in cuts.items()}, "next_booking": {},
           "by_persona": {}, "by_cutoff": {}}
    for split, cs in cuts.items():
        if splits and split not in splits:
            continue
        tops = {m: [] for m in models}
        slots = {m: [] for m in models}
        per_cut = {m: [] for m in models}
        for c in cs:
            actual = actual_bookings(df, c, window)
            for m in models:
                top, week = PREDICTORS[m](df, c, cfg, calendar, window)
                s = score_top1(top, actual, tol).assign(cutoff=c.isoformat())
                sc = score_slots(week, actual, tol)
                tops[m].append(s)
                slots[m].append(sc)
                per_cut[m].append({"cutoff": c.isoformat(), **_summary(s, [sc])})
        out["next_booking"][split] = {m: _summary(pd.concat(tops[m]), slots[m]) for m in models}
        out["by_cutoff"][split] = per_cut
        if personas:
            out["by_persona"][split] = {}
            for m in models:
                t = pd.concat(tops[m])
                t["persona"] = t["user_id"].map(personas).fillna("unknown")
                out["by_persona"][split][m] = {p: {"hit_at_1": round(float(g["hit"].mean()), 4), "n": int(len(g))}
                                               for p, g in t.groupby("persona")}
    return out


# ── pattern recovery vs truth.json ───────────────────────────────────────────

def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def recovery(predicted: dict[str, list[dict]], truth: dict, end: date) -> dict:
    """Per persona: day-set Jaccard, exact day sets, lot accuracy, time error for users with a
    routine active at `end`; false-habit rate for users with none.

    `predicted`: user_id → [{"days": [...], "location_id": ..., "center": "HH:MM"}]."""
    recs = []
    for uid, t in truth["users"].items():
        active = [r for r in t.get("routines", [])
                  if date.fromisoformat(r["active_from"]) <= end
                  and (r.get("active_to") is None or date.fromisoformat(r["active_to"]) >= end)]
        preds = predicted.get(uid, [])
        if not active:
            recs.append({"persona": t["persona"], "has_truth": False, "false_habit": bool(preds)})
            continue
        for r in active:
            def score(p):
                return _jaccard(set(r["days"]), set(p["days"])) + (p["location_id"] == r["location_id"])
            best = max(preds, key=score, default=None)
            if best is None:
                recs.append({"persona": t["persona"], "has_truth": True, "jaccard": 0.0, "exact_days": False,
                             "lot_correct": False, "time_err_min": None})
                continue
            h1, m1 = map(int, r["center"].split(":"))
            h2, m2 = map(int, best["center"].split(":"))
            recs.append({"persona": t["persona"], "has_truth": True,
                         "jaccard": _jaccard(set(r["days"]), set(best["days"])),
                         "exact_days": set(r["days"]) == set(best["days"]),
                         "lot_correct": best["location_id"] == r["location_id"],
                         "time_err_min": float(abs(circ_diff(h1 * 60 + m1, h2 * 60 + m2)))})
    df = pd.DataFrame(recs)

    def agg(g: pd.DataFrame) -> dict:
        tr, no = g[g["has_truth"]], g[~g["has_truth"]]
        out = {"routines": int(len(tr)), "users_without_routine": int(len(no))}
        if len(tr):
            out |= {"day_jaccard": round(float(tr["jaccard"].mean()), 4),
                    "exact_day_set": round(float(tr["exact_days"].mean()), 4),
                    "lot_accuracy": round(float(tr["lot_correct"].mean()), 4),
                    "time_err_min": round(float(tr["time_err_min"].dropna().mean()), 1)
                    if tr["time_err_min"].notna().any() else None}
        if len(no):
            out["false_habit_rate"] = round(float(no["false_habit"].mean()), 4)
        return out

    return {"overall": agg(df), "by_persona": {p: agg(g) for p, g in df.groupby("persona")}}


def engine_routines(an: dict) -> dict[str, list[dict]]:
    from rhythm.stages.common import DAY_NAMES, hhmm
    return {uid: [{"days": [DAY_NAMES[d] for d in r.days], "location_id": r.location_id, "center": hhmm(r.center)}
                  for r in g.itertuples()] for uid, g in an["routines"].groupby("user_id")}


def b2_routines(df: pd.DataFrame, cutoff: date, cfg: dict) -> dict[str, list[dict]]:
    out = {}
    for uid, ub in history(df, cutoff).groupby("user_id"):
        r = baselines.user_patterns(uid, ub, cutoff, cfg, "eval")
        if r["pattern_type"] == "recurring":
            out[uid] = [{"days": r["days"], "location_id": r["location_id"], "center": r["time_window"]["center"]}]
    return out


# ── entry point ───────────────────────────────────────────────────────────────

def evaluate(input_path: str | Path, truth_path: str | Path | None = None, run_id: str | None = None,
             cfg: dict | None = None) -> dict:
    """Backtest + recovery; writes `metrics.json` into the run folder (a fresh run unless `run_id`)."""
    cfg = cfg or load_config()
    calendar = load_calendar()
    df = load_bookings(input_path)
    truth_path = Path(truth_path) if truth_path else Path(input_path).with_name("truth.json")
    truth = json.loads(truth_path.read_text(encoding="utf-8")) if truth_path.exists() else None
    personas = {u: t["persona"] for u, t in truth["users"].items()} if truth else None
    run_path = resolve_run(run_id) if run_id else None
    if run_path is None:
        info = run(input_path)
        run_path = resolve_run(info["run_id"])

    end = default_cutoff(df)
    metrics = {"run_id": run_path.name, "input": str(input_path), "end": end.isoformat(),
               "definitions": {
                   "hit_at_1": "top-1 predicted next booking matches the user's first kept booking of the "
                               f"next {cfg['evaluate']['window_days']} days: same day and lot, start within "
                               f"±{cfg['predict']['hit_tolerance_minutes']} min; over users who booked in that window",
                   "time_mae_min": "mean |start error| where day and lot matched",
                   "coverage": "share of those users that got a prediction",
                   "slot_precision/recall": "week-ahead predicted slots vs real bookings (same day + lot, ±tol)",
                   "B1": "repeat last week", "B2": "modal weekdays/time/lot over 8 weeks",
                   "B3": "frequency rules: (lot, weekday) share of weeks ≥ 0.5"},
               **backtest(df, cfg, calendar, personas)}
    if truth:
        an = analyse(df, end, cfg, calendar)
        metrics["recovery"] = {"engine": recovery(engine_routines(an), truth, end),
                               "B2": recovery(b2_routines(df, end, cfg), truth, end)}
    (run_path / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    return metrics


def headline(metrics: dict) -> str:
    """Markdown table of the test-split numbers."""
    lines = ["| model | Hit@1 | time MAE (min) | coverage | slot P | slot R | slot F1 |", "|---|---|---|---|---|---|---|"]
    for m, s in metrics["next_booking"]["test"].items():
        lines.append(f"| {m} | {s['hit_at_1']} | {s['time_mae_min']} | {s['coverage']} | "
                     f"{s['slot_precision']} | {s['slot_recall']} | {s['slot_f1']} |")
    return "\n".join(lines)
