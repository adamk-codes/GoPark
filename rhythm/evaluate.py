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

import copy
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from rhythm import baselines
from rhythm.io import load_bookings, load_calendar, load_config, resolve_run
from rhythm.pipeline import (MODEL_VERSION, analyse, default_cutoff, fit_from_slots, history,
                             kept_bookings, priors_and_forecast, run, scored_slots)
from rhythm.stages import calibrate, remind
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


actual_bookings = kept_bookings   # kept (completed / no-show) bookings in (cutoff, cutoff + window]


# ── predictions: each returns (top-1 next booking per user, predicted slots for the window) ──

def predict_engine(df, cutoff, cfg, calendar, window_days, an: dict | None = None):
    an = an or analyse(df, cutoff, cfg, calendar)
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
    f["weekday"] = f["day"].dt.day_of_week
    f["week"] = f["day"] - pd.to_timedelta(f["weekday"], unit="D")
    first_week = f.groupby("user_id")["week"].min()
    weeks = ((pd.Timestamp(cutoff) - first_week).dt.days // 7 + 1).rename("weeks")
    g = f.groupby(["user_id", "location_id", "weekday"]).agg(n=("week", "nunique"), minute=("minute", "median"))
    g = g.join(weeks, on="user_id").reset_index()
    g["p"] = (g["n"] / g["weeks"]).clip(upper=1.0)
    days = pd.DataFrame({"day": pd.to_datetime([cutoff + timedelta(days=k) for k in range(1, 8)])})
    days["weekday"] = days["day"].dt.day_of_week
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
             models: list[str] | None = None, splits: list[str] | None = None, engine_an=None) -> dict:
    """Hit@1 / time MAE / slot P-R per split × model (and per persona if given).
    `engine_an(cutoff)` supplies the engine's (calibrated) analysis; default: a fresh raw one."""
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
                if m == "engine" and engine_an is not None:
                    top, week = predict_engine(df, c, cfg, calendar, window, an=engine_an(c))
                else:
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
                  for r in g.itertuples()] for uid, g in current_routines(an).groupby("user_id")}


def current_routines(an: dict) -> pd.DataFrame:
    """Routines the engine says the user has now (state ≠ ended)."""
    r = an["routines"]
    return r[r["state"] != "ended"] if "state" in r else r


def b2_routines(df: pd.DataFrame, cutoff: date, cfg: dict) -> dict[str, list[dict]]:
    out = {}
    for uid, ub in history(df, cutoff).groupby("user_id"):
        r = baselines.user_patterns(uid, ub, cutoff, cfg, "eval")
        if r["pattern_type"] == "recurring":
            out[uid] = [{"days": r["days"], "location_id": r["location_id"], "center": r["time_window"]["center"]}]
    return out


# ── the engine at many cutoffs: one cache, calibration fitted on validation only ─

class Engine:
    """Raw analyses per cutoff for one config (cached), plus the isotonic map and τ fitted on
    the validation cutoffs. `an(c)` is what `rhythm run` would output at `c`, except that the
    calibration always comes from the validation split (never from test weeks)."""

    def __init__(self, df: pd.DataFrame, cfg: dict, calendar: dict | None, validation: list[date],
                 raw: dict | None = None):
        self.df, self.cfg, self.calendar, self.validation = df, cfg, calendar, validation
        self.raw = raw if raw is not None else {}
        self._cal, self.val_slots = None, None

    def raw_an(self, c: date) -> dict:
        if c not in self.raw:
            self.raw[c] = analyse(self.df, c, self.cfg, self.calendar)
        return self.raw[c]

    @property
    def calibration(self) -> dict:
        if self._cal is None:
            self.val_slots = scored_slots(self.df, self.validation, self.cfg, self.calendar,
                                          {c: self.raw_an(c) for c in self.validation})
            self._cal = fit_from_slots(self.val_slots, self.cfg)
        return self._cal

    def an(self, c: date) -> dict:
        a, cal = self.raw_an(c), self.calibration
        return {**a, "slots": a["slots"].assign(p=calibrate.apply(a["slots"]["score"], cal)), "calibration": cal}

    def slots(self, cutoffs: list[date]) -> pd.DataFrame:
        return scored_slots(self.df, cutoffs, self.cfg, self.calendar, {c: self.raw_an(c) for c in cutoffs})


def calibration_report(eng: Engine, test: list[date]) -> tuple[dict, dict]:
    """→ (calibration: map + Brier / ECE / reliability bins before & after per split,
          reminders: τ and the precision/recall curve per split)."""
    cal = eng.calibration
    out = {"map": {"x": cal["x"], "y": cal["y"]}, "fitted": cal["fitted"], "fitted_on": cal["fitted_on"],
           "n": cal["n"]}
    rem = {"target_precision": eng.cfg["reminder"]["target_precision"], "tau": cal["tau"]}
    for split, s in (("validation", eng.val_slots), ("test", eng.slots(test))):
        p = calibrate.apply(s["score"], cal)
        out[split] = {"before": calibrate.reliability(s["score"].clip(0, 1), s["y"]),
                      "after": calibrate.reliability(p, s["y"])}
        n_actual = int(s.groupby("cutoff")["n_actual"].first().sum())
        rem[split] = remind.pr_curve(p, s["y"].to_numpy(), n_actual)
        at = [c for c in rem[split] if c["tau"] == round(cal["tau"], 2)]
        rem[f"{split}_at_tau"] = at[0] if at else None
    return out, rem


# ── drift detection delay vs truth.json ──────────────────────────────────────

def _matches(routine: dict, p: dict) -> bool:
    h1, m1 = map(int, routine["center"].split(":"))
    h2, m2 = map(int, p["center"].split(":"))
    return (p["location_id"] == routine["location_id"] and _jaccard(set(routine["days"]), set(p["days"])) >= 0.5
            and abs(circ_diff(h1 * 60 + m1, h2 * 60 + m2)) <= 45)


def drift_events(truth: dict, start: date, end: date) -> list[dict]:
    """Routine starts after the history began and routine ends before `end`, from truth.json."""
    ev = []
    for uid, t in truth["users"].items():
        for r in t.get("routines", []):
            a = date.fromisoformat(r["active_from"])
            if start + timedelta(weeks=2) < a < end:
                ev.append({"user_id": uid, "persona": t["persona"], "kind": "started", "date": a, "routine": r})
            if r.get("active_to") and date.fromisoformat(r["active_to"]) < end:
                ev.append({"user_id": uid, "persona": t["persona"], "kind": "ended",
                           "date": date.fromisoformat(r["active_to"]) + timedelta(days=1), "routine": r})
    return ev


def drift_delays(events: list[dict], cutoffs: list[date], current_at, ended_at=None) -> dict:
    """Days from each truth change to the first weekly cutoff where the model shows it: a
    started routine is among its current routines; an ended one no longer is.
    `current_at(c)` → {user: [routine dicts]}; `ended_at(c)` → {user: [ended routine dicts with
    change_date]} (engine only) for the change-date error."""
    rows = []
    for e in events:
        cs = [c for c in cutoffs if c >= e["date"]]
        hit, change = None, None
        for c in cs:
            mine = current_at(c).get(e["user_id"], [])
            found = any(_matches(e["routine"], p) for p in mine)
            if (e["kind"] == "started") == found:
                hit = c
                pool = mine if e["kind"] == "started" else (ended_at(c).get(e["user_id"], []) if ended_at else [])
                m = [p for p in pool if _matches(e["routine"], p) and p.get("change_date")]
                change = m[0]["change_date"] if m else None
                break
        rows.append({"user_id": e["user_id"], "persona": e["persona"], "kind": e["kind"],
                     "truth_date": e["date"].isoformat(), "detected_on": hit.isoformat() if hit else None,
                     "delay_days": (hit - e["date"]).days if hit else None,
                     "change_date": change,
                     "change_date_error_days": abs((date.fromisoformat(change) - e["date"]).days) if change else None,
                     "observable": bool(cs)})
    df = pd.DataFrame(rows)
    summary = {}
    for kind, g in (df.groupby("kind") if len(df) else []):
        g = g[g["observable"]]
        d = g["delay_days"].dropna()
        ce = g["change_date_error_days"].dropna()
        summary[kind] = {"events": int(len(g)),
                         "detected": round(float(g["delay_days"].notna().mean()), 4) if len(g) else None,
                         "median_delay_days": float(d.median()) if len(d) else None,
                         "mean_delay_days": round(float(d.mean()), 1) if len(d) else None,
                         "median_change_date_error_days": float(ce.median()) if len(ce) else None}
    return {"summary": summary, "events": rows}


def _routine_dicts(r: pd.DataFrame) -> dict[str, list[dict]]:
    from rhythm.stages.common import DAY_NAMES, hhmm
    return {uid: [{"days": [DAY_NAMES[d] for d in x.days], "location_id": x.location_id, "center": hhmm(x.center),
                   "change_date": x.change_date.date().isoformat() if pd.notna(x.change_date) else None}
                  for x in g.itertuples()] for uid, g in r.groupby("user_id")}


# ── lot forecast error ───────────────────────────────────────────────────────

def forecast_error(df: pd.DataFrame, eng: Engine, cutoffs: list[date], cfg: dict, window: int) -> dict:
    """Expected vs real arrivals (completed bookings) per lot × date × hour over the week after
    each cutoff, for the engine's forecast and B2's."""
    errs = {"engine": [], "B2": []}
    d = df["start_time"].dt.date
    completed = normalise_status(df["status"]) == "completed"
    for c in cutoffs:
        done = df[(d > c) & (d <= c + timedelta(days=window)) & completed]
        actual = done.groupby([done["location_id"], done["start_time"].dt.date.map(date.isoformat),
                               done["start_time"].dt.hour]).size()
        hist = history(df, c)
        b2_rows = [baselines.user_patterns(uid, ub, c, cfg, "eval") for uid, ub in hist.groupby("user_id")]
        for name, fc in (("engine", priors_and_forecast(eng.an(c), days_ahead=window)[1]),
                         ("B2", baselines.priors_and_forecast(b2_rows, c, days_ahead=window)[1])):
            f = pd.Series({(r["lot_id"], r["date"], r["hour"]): r["expected_arrivals"] for r in fc}, dtype=float)
            errs[name].append(pd.concat([f.rename("f"), actual.rename("a")], axis=1).fillna(0.0))
    out = {}
    for name, parts in errs.items():
        b = pd.concat(parts)
        e = b["f"] - b["a"]
        out[name] = {"mae_per_lot_hour": round(float(e.abs().mean()), 3),
                     "rmse_per_lot_hour": round(float(np.sqrt((e ** 2).mean())), 3),
                     "bias_per_lot_hour": round(float(e.mean()), 3),
                     "cells": int(len(b)), "actual_arrivals": int(b["a"].sum()),
                     "expected_arrivals": round(float(b["f"].sum()), 1)}
    return out


# ── ablation ─────────────────────────────────────────────────────────────────

ABLATIONS = {"full": None, "-prior": "prior", "-decay": "decay", "-drift": "drift",
             "-activity": "activity", "-calibration": "calibrate"}


def ablation(df: pd.DataFrame, cfg: dict, calendar: dict | None, cuts: dict, truth: dict | None, end: date,
             full: Engine) -> list[dict]:
    """Full engine vs one stage switched off at a time, on the test split (+ recovery at the end)."""
    rows = []
    for name, stage in ABLATIONS.items():
        if stage is None:
            eng = full
        else:
            v = copy.deepcopy(cfg)
            v["stages"] = {**v.get("stages", {}), stage: False}
            # calibration doesn't change the raw analyses, so it can share the full engine's cache
            eng = Engine(df, v, calendar, cuts["validation"], raw=full.raw if stage == "calibrate" else None)
        bt = backtest(df, eng.cfg, calendar, models=["engine"], splits=["test"], engine_an=eng.an)
        s = bt["next_booking"]["test"]["engine"]
        cal, rem = calibration_report(eng, cuts["test"])
        row = {"variant": name, "hit_at_1": s["hit_at_1"], "time_mae_min": s["time_mae_min"],
               "slot_f1": s["slot_f1"], "brier": cal["test"]["after"]["brier"], "ece": cal["test"]["after"]["ece"],
               "tau": rem["tau"], "reminder_precision": (rem["test_at_tau"] or {}).get("precision"),
               "reminder_recall": (rem["test_at_tau"] or {}).get("recall")}
        if truth:
            r = recovery(engine_routines(eng.raw_an(end)), truth, end)["overall"]
            row |= {"day_jaccard": r.get("day_jaccard"), "false_habit_rate": r.get("false_habit_rate")}
        rows.append(row)
    return rows


# ── entry point ───────────────────────────────────────────────────────────────

DEFINITIONS = {
    "hit_at_1": "top-1 predicted next booking matches the user's first kept booking of the next window: "
                "same day and lot, start within ±tolerance; over users who booked in that window",
    "time_mae_min": "mean |start error| where day and lot matched",
    "coverage": "share of those users that got a prediction",
    "slot_precision/recall": "week-ahead predicted routine slots vs real bookings (same day + lot, ±tolerance)",
    "calibration": "isotonic map fitted on the validation cutoffs' candidate slots only; Brier / ECE "
                   "(10 equal-width bins): before = raw score, after = calibrated",
    "reminders": "remind when calibrated p ≥ τ; precision = reminded slots that were booked, recall = booked "
                 "slots that were reminded; τ = lowest with validation precision ≥ target",
    "forecast": "expected vs completed arrivals per lot × date × hour, week after each test cutoff",
    "recovery": "vs truth.json at the end (and the last validation cutoff): best-matching current routine "
                "(state ≠ ended) per true routine; false_habit_rate = users with no true routine that get a current one",
    "drift": "days from a true routine start / end to the first weekly cutoff where the model shows it "
             "(7-day granularity); change_date error for the engine",
    "ablation": "test split with one stage switched off; recovery at the end",
    "B1": "repeat last week", "B2": "modal weekdays/time/lot over 8 weeks",
    "B3": "frequency rules: (lot, weekday) share of weeks ≥ 0.5"}


def evaluate(input_path: str | Path, truth_path: str | Path | None = None, run_id: str | None = None,
             cfg: dict | None = None, with_ablation: bool = True) -> dict:
    """Backtest, calibration, reminders, forecast error, recovery, drift delay and ablation;
    writes `metrics.json` into the run folder (a fresh run unless `run_id`). Keys: CONTRACT.md §8."""
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
    window = cfg["evaluate"]["window_days"]
    cuts = split_cutoffs(end, cfg)
    eng = Engine(df, cfg, calendar, cuts["validation"])
    metrics = {"run_id": run_path.name, "input": str(input_path), "end": end.isoformat(),
               "model_version": MODEL_VERSION,
               "settings": {"window_days": window, "hit_tolerance_minutes": cfg["predict"]["hit_tolerance_minutes"]},
               "definitions": DEFINITIONS,
               **backtest(df, cfg, calendar, personas, engine_an=eng.an)}
    metrics["calibration"], metrics["reminders"] = calibration_report(eng, cuts["test"])
    metrics["forecast"] = forecast_error(df, eng, cuts["test"], cfg, window)
    if truth:
        last_val = cuts["validation"][-1]
        metrics["recovery"] = {"engine": recovery(engine_routines(eng.raw_an(end)), truth, end),
                               "B2": recovery(b2_routines(df, end, cfg), truth, end)}
        metrics["recovery_validation"] = {
            "cutoff": last_val.isoformat(),
            "engine": recovery(engine_routines(eng.raw_an(last_val)), truth, last_val),
            "B2": recovery(b2_routines(df, last_val, cfg), truth, last_val)}
        metrics["false_habit"] = {
            m: {p: s["false_habit_rate"] for p, s in r["by_persona"].items() if "false_habit_rate" in s}
            | {"overall": r["overall"].get("false_habit_rate")} for m, r in metrics["recovery"].items()}
        metrics["drift"] = drift_report(df, eng, truth, personas, end, cfg)
    if with_ablation:
        metrics["ablation"] = ablation(df, cfg, calendar, cuts, truth, end, eng)
    metrics["charts"] = charts(metrics)
    (run_path / "metrics.json").write_text(json.dumps(metrics, indent=1, default=str), encoding="utf-8")
    return metrics


def drift_report(df: pd.DataFrame, eng: Engine, truth: dict, personas: dict, end: date, cfg: dict) -> dict:
    start = df["start_time"].min().date()
    scan = [end - timedelta(weeks=k) for k in range(max(0, (end - start).days // 7 - 3), -1, -1)]
    events = drift_events(truth, start, end)
    cache: dict = {}

    def memo(kind, c, make):
        if (kind, c) not in cache:
            cache[(kind, c)] = make()
        return cache[(kind, c)]

    def eng_current(c):
        return memo("cur", c, lambda: _routine_dicts(current_routines(eng.raw_an(c))))

    def eng_ended(c):
        r = eng.raw_an(c)["routines"]
        return memo("end", c, lambda: _routine_dicts(r[r["state"] == "ended"]))

    st = eng.raw_an(end)["routines"]
    return {"cutoffs": [c.isoformat() for c in scan],
            "engine": drift_delays(events, scan, eng_current, eng_ended),
            "B2": drift_delays(events, scan, lambda c: memo("b2", c, lambda: b2_routines(df, c, cfg))),
            "states_at_end": {p: st[st["user_id"].map(personas) == p]["state"].value_counts().to_dict()
                              for p in sorted(set(personas.values()))}}


def charts(m: dict) -> dict:
    """Chart-ready views of the metrics above, so the dashboard only renders them."""
    splits = [s for s in ("validation", "test") if s in m["by_cutoff"]]
    out = {"hit_at_1_by_cutoff": {
        "cutoffs": [r["cutoff"] for s in splits for r in m["by_cutoff"][s]["engine"]],
        "split": [s for s in splits for _ in m["by_cutoff"][s]["engine"]],
        "series": {model: [r["hit_at_1"] for s in splits for r in m["by_cutoff"][s][model]]
                   for model in m["by_cutoff"]["test"]}},
        "reliability": {"before": m["calibration"]["test"]["before"]["bins"],
                        "after": m["calibration"]["test"]["after"]["bins"]},
        "reminder_curve": {"tau": m["reminders"]["tau"], "target_precision": m["reminders"]["target_precision"],
                           "points": m["reminders"]["test"]},
        "forecast": [{"model": k, **v} for k, v in m["forecast"].items()]}
    nb = m["next_booking"]["test"]
    out["headline"] = {"hit_at_1": {k: v["hit_at_1"] for k, v in nb.items()},
                       "slot_f1": {k: v["slot_f1"] for k, v in nb.items()},
                       "brier": {"before": m["calibration"]["test"]["before"]["brier"],
                                 "after": m["calibration"]["test"]["after"]["brier"]},
                       "ece": {"before": m["calibration"]["test"]["before"]["ece"],
                               "after": m["calibration"]["test"]["after"]["ece"]},
                       "reminder_at_tau": m["reminders"]["test_at_tau"]}
    if "recovery" in m:
        e, b = m["recovery"]["engine"], m["recovery"]["B2"]
        out["headline"]["day_jaccard"] = {"engine": e["overall"].get("day_jaccard"), "B2": b["overall"].get("day_jaccard")}
        out["headline"]["false_habit_rate"] = {"engine": e["overall"].get("false_habit_rate"),
                                               "B2": b["overall"].get("false_habit_rate")}
        hit = m.get("by_persona", {}).get("test", {})
        out["persona_table"] = [
            {"persona": p, "routines": s.get("routines"),
             "engine_day_jaccard": s.get("day_jaccard"), "B2_day_jaccard": b["by_persona"].get(p, {}).get("day_jaccard"),
             "engine_lot_accuracy": s.get("lot_accuracy"), "B2_lot_accuracy": b["by_persona"].get(p, {}).get("lot_accuracy"),
             "engine_false_habit": s.get("false_habit_rate"),
             "B2_false_habit": b["by_persona"].get(p, {}).get("false_habit_rate"),
             "engine_hit_at_1": hit.get("engine", {}).get(p, {}).get("hit_at_1"),
             "B2_hit_at_1": hit.get("B2", {}).get(p, {}).get("hit_at_1")}
            for p, s in e["by_persona"].items()]
    if "drift" in m:
        out["drift_delay"] = [{"model": model, "kind": k, **v} for model in ("engine", "B2")
                              for k, v in m["drift"][model]["summary"].items()]
    if "ablation" in m:
        out["ablation"] = m["ablation"]
    return out


def headline(metrics: dict) -> str:
    """Markdown tables of the headline numbers (test split)."""
    lines = ["| model | Hit@1 | time MAE (min) | coverage | slot P | slot R | slot F1 |", "|---|---|---|---|---|---|---|"]
    for m, s in metrics["next_booking"]["test"].items():
        lines.append(f"| {m} | {s['hit_at_1']} | {s['time_mae_min']} | {s['coverage']} | "
                     f"{s['slot_precision']} | {s['slot_recall']} | {s['slot_f1']} |")
    c, r, f = metrics["calibration"]["test"], metrics["reminders"], metrics["forecast"]
    at = r["test_at_tau"] or {}
    lines.append(f"\ncalibration (test): Brier {c['before']['brier']} -> {c['after']['brier']}, "
                 f"ECE {c['before']['ece']} -> {c['after']['ece']}; tau {r['tau']} "
                 f"(test precision {at.get('precision')}, recall {at.get('recall')})")
    lines.append(f"forecast MAE per lot-hour: engine {f['engine']['mae_per_lot_hour']} vs B2 {f['B2']['mae_per_lot_hour']}")
    if "drift" in metrics:
        for model in ("engine", "B2"):
            for k, v in metrics["drift"][model]["summary"].items():
                lines.append(f"drift {model} {k}: {v['events']} events, detected {v['detected']}, median delay "
                             f"{v['median_delay_days']} d, change-date error {v['median_change_date_error_days']} d")
    if "ablation" in metrics:
        lines += ["", "| variant | Hit@1 | slot F1 | day Jaccard | false habit | Brier | ECE |", "|---|---|---|---|---|---|---|"]
        for a in metrics["ablation"]:
            lines.append(f"| {a['variant']} | {a['hit_at_1']} | {a['slot_f1']} | {a.get('day_jaccard')} | "
                         f"{a.get('false_habit_rate')} | {a['brier']} | {a['ece']} |")
    return "\n".join(lines)
