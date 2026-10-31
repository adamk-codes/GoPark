# Brief P3 — Rhythm engine (Challenge 2)

**Branch:** `feat/rhythm` · **You own:** `rhythm/`, `api/rhythm_api/`, `config/rhythm.yaml`
**Read:** `CONTRACT.md` §3 and §8, `ARCHITECTURE_PLAN.md` §4 (the full engine design) and §5.3

## Mission
Replace the baseline with the real engine. It must:
- find several routines per user;
- separate habit from coincidence;
- notice routines changing;
- predict the next booking with **calibrated** confidence;
- recommend reminders by value tier;
- explain every row;
- prove all of it with a time-based backtest that beats the baselines.

Your outputs also feed Challenge 1 (`arrival_priors.json`, `lot_forecast.json`), so their shapes must not change.

## Independence rule (important)
Judges will ask whether the engine was tuned to the fake data. So:
- **don't read `tools/generator/`** code;
- **only `rhythm/evaluate.py` may open `data/truth.json`;**
- tune thresholds on the **validation** weeks only, never on test weeks.

## Where it stands (P0)
- `rhythm/io.py`: loading (CSV/JSON, times to `Asia/Beirut`, duplicates dropped), run-folder writing, CSV flattening.
- `rhythm/baselines.py`: **B2** (modal weekdays/time/lot over 8 weeks). It's the stand-in and stays as a baseline. Known weakness: e.g. U0175 is truly Mon/Wed/Fri but B2 says "Mon / Fri".
- `rhythm/pipeline.py`: `run()`; replace `_patterns()` with the real pipeline. `priors_and_forecast()` comes from baselines today.
- `api/rhythm_api/`: runs, patterns (json/csv + filters), priors, forecast, users/{id} (history + heatmap), evaluation (reads `metrics.json`).

## Phase P1 (hours 3–12): core pipeline
Put each stage in `rhythm/stages/<name>.py` as pure functions on DataFrames, so the ablation can switch them off.
- [ ] **clean:** status weights from config; merge a cancel + rebook within 24 h for the same slot; repair messy rows (P4 adds an injector: inconsistent status case, missing end, end < start, duplicates); compute `cancel_rate`.
- [ ] **activity:** active weeks per user, minus holiday/term-break weeks (`config/holidays.yaml`) and inactive gaps (`max(21 d, 3 × median gap)`).
- [ ] **discover:** per user × location, DBSCAN on start minute on a circular clock (eps 45 min). Each cluster's weekdays become candidate routines. Several routines per user.
- [ ] **habit:**
  - Beta-Binomial with weekly decay (half-life 4 weeks) and an empirical-Bayes population prior per (location, weekday), worth about 2 weeks of evidence;
  - plus a binomial chance test against the user's own random-weekday rate;
  - thresholds in `config/rhythm.yaml`;
  - day-set labels: "Weekdays", "Daily", "Weekends", "Mon / Wed / Fri".
- [ ] Output rows exactly per `pattern_row` (one primary row per user + one row per extra routine). `uv run pytest` must stay green.
- [ ] **`rhythm/evaluate.py` v1** + CLI `python -m rhythm evaluate`:
  - splits from config (last 4 weeks test, 4 before that validation);
  - rolling weekly cutoffs;
  - **Hit@1** (same day + lot, start ±60 min), time MAE;
  - against **B1** (repeat last week), **B2**, **B3** (frequency rules);
  - writes `metrics.json` into the run folder.

**Done when:** the engine beats B2 on Hit@1 on the test weeks, and U0175-type users get their full day set.

## Phase P2 (hours 12–24): drift, prediction, calibration, reminders
- [ ] **drift:**
  - `emerging` / `active` / `fading` / `ended` per routine (rules in ARCHITECTURE_PLAN §4.2 step 5);
  - `change_date` from CUSUM on weekly hits;
  - a routine switch (emerging + ended together) gets a combined reason.
- [ ] **predict:**
  - 14-day candidate slots; score = posterior mean × activity factor;
  - occasional users get a gap/hazard model ("likely within 7 days at Hamra, ~40%");
  - `inactivity_detected`.
- [ ] **calibrate:** isotonic regression fitted on validation cutoffs, applied to all scores. In `metrics.json`: Brier, ECE, and reliability-diagram bins before and after.
- [ ] **remind:**
  - τ chosen where validation precision ≥ `target_precision`;
  - tiers `locked_in` / `at_risk` / `emerging` / `none`;
  - timing = median booking lead time from `created_at` + 30 min, or the heuristic fallback;
  - 48 h cooldown; suppress if the slot is already booked.
  - In `metrics.json`: precision/recall per τ (for the dashboard slider).
- [ ] **explain:** template reasons with real numbers (examples in ARCHITECTURE_PLAN §4.2 step 9).
- [ ] **evaluate v2:**
  - pattern recovery vs truth (day-set Jaccard, lot accuracy, time error);
  - false-habit rate on coincidence-trap users;
  - drift detection delay;
  - all of it per persona.

## Phase P3 (hours 24–32): feeds for C1, ablation, hidden personas
- [ ] **`arrival_priors.json` and `lot_forecast.json` from the real pipeline.** The forecast is the sum of calibrated probabilities + a base rate for unpredictable users. Shapes unchanged, because P2's fusion and the attendant "expected soon" list read them.
- [ ] **Ablation table** in `metrics.json`: full vs −prior, −decay, −drift, −activity windows, −calibration.
- [ ] **Hidden-persona run:** P4 hands over a dataset with 2 persona types you've never seen. Run evaluate once and report the results as they come out.
- [ ] Evaluation endpoint returns chart-ready data (bins, curves, tables) so the dashboard only renders it. Agree the exact keys with P4 and add them to `CONTRACT.md` §8.
- [ ] *(Stretch)* LightGBM booster, kept only if the ablation shows a gain.

## Phase P4 (hours 32–40)
- [ ] Freeze metrics on the final dataset, export a static `evaluation_report.html` (optional) and give P4 the 3–4 headline numbers for the slides.
- [ ] *(Stretch)* Speed benchmark at 100k / 1M bookings.

## Interfaces
| You provide | To | Note |
|---|---|---|
| `patterns.json/csv` | P4 dashboard, judges | Shape fixed by `pattern_row` schema |
| `arrival_priors.json` | P2 fusion, overview "expected soon" | Shape fixed |
| `lot_forecast.json` | P4 dashboard, attendant app | Shape fixed |
| `metrics.json` keys | P4 evaluation page | Agree keys in P3 |

| You consume | From |
|---|---|
| `data/bookings.csv` (+ `created_at`) | P4 generator |
| Full persona set, messy rows, hidden personas | P4 (P1 / P3 phases) |

## Gotchas
- History ends Sun 2026-11-15; rows after that have status `booked` and must never be training data.
- Time zones: summer is +03:00, winter +02:00. Work in local minutes-of-day (already converted by `load_bookings`).
- Keep `python -m rhythm run` under ~30 s on 300 users so live re-runs in the demo stay quick.
