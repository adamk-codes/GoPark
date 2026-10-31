# GoPark × AUB Hackathon — Challenge 02 Architecture Plan

> Working name: **GoPark Rhythm** — booking pattern recognition you can trust
> Status: v2.0 · 2026-10-31 · **Challenge 2 only** (the combined C1+C2 plan is archived in `ARCHITECTURE_PLAN_combined_v1.md`)
> Constraints: 36–48 h · 3–4 people · synthetic data only · no LLM · English UI

---

## 0. TL;DR

A **rule/ML hybrid engine** that turns raw booking records into a per-user pattern table whose confidence scores **actually mean something**, plus the evidence to prove it.

```
bookings ─▶ Clean ─▶ Discover routines ─▶ Habit test ─▶ Drift ─▶ Predict next ─▶ Calibrate ─▶ Reminder policy ─▶ Explain
                                                                      │                                         │
                                                         (optional LightGBM booster)          pattern table · CSV/JSON/API
                                                                                              lot demand forecast · segments
```

Three things set this apart from the obvious "count bookings per weekday" solution:
1. **Calibrated confidence.** "80%" really comes true about 80% of the time, shown with a reliability chart.
2. **Statistically grounded habit detection.** A Bayesian recurrence score with a population prior, plus a test against chance. Several routines per user, each labelled **emerging / active / fading / ended**.
3. **Honest evaluation.** A time-based backtest against 3 baselines, an ablation table, robustness curves, accuracy by persona, **hidden personas** the engine author never saw, and a scale benchmark.

On top of that, the engine produces outputs GoPark can act on: **reminder targets ranked by value**, a **lot-level demand forecast** built from individual predictions, and **user segments**.

---

## 1. The problem

### 1.1 Brief, decoded
| Brief says | Means in practice |
|---|---|
| Input: user, booking ID, location, start, end, status | No booking-creation time, no coordinates, no payments. We must work with very little. |
| Output: pattern, time/place, next likely booking, confidence | A row per user (plus a row per routine), as structured data |
| Extras: last booking, frequency, inactivity, **reminder timing**, **reason** | Reasons must be readable by a non-technical ops person |
| Habit vs coincidence | Needs a statistical answer, not a hard-coded "≥ 3 times" rule |
| Multiple patterns, changing routines | Per-user routine set plus drift detection |
| Sparse histories, cancellations, inconsistent times | Shrinkage toward a population prior, weighting by status, time tolerance on a circular clock |
| When is confidence too low to remind | A threshold chosen from data, justified by precision vs recall |
| Working model, CSV/JSON/API, held-out evaluation | Engine + export + backtest are all mandatory |

### 1.2 Judging weights → what we optimise
| Criterion | Weight | Our lever |
|---|---|---|
| Innovation | 25% | Drift states, statistical habit test, value-ranked reminders, demand forecast from individual predictions |
| Technical | 25% | Calibration, nested time splits, baselines, ablation, hidden-persona test |
| Feasibility & scale | 20% | Vectorised engine, 1M-booking benchmark, API, incremental runs, works on any CSV with the same columns |
| UX & clarity | 15% | Readable reasons, per-user heatmap + timeline, one-click export, bring-your-own-CSV upload |
| Value for GoPark | 15% | Reminder uplift estimate, demand forecast, inactivity (churn) flags, segments |

---

## 2. Solution paths explored

| # | Approach | Strengths | Weaknesses | Verdict |
|---|---|---|---|---|
| A | **Frequency rules** (count weekday × hour × location, confidence = share) | Fast, readable | Can't tell habit from coincidence; the confidence isn't a probability; brittle on sparse users | **Baseline B3** |
| B | **Bayesian recurrence** (Beta-Binomial per slot, time decay, population prior) | Handles sparse data, gives real probabilities, readable | Needs good slot definitions | **Core scorer** |
| C | **Clustering** (DBSCAN on circular time × location × weekday) | Finds several routines per user, tolerates time jitter | Parameter tuning | **Routine discovery** |
| D | **Change-point detection** (two-window test / CUSUM on weekly hits) | Catches routine changes | Needs ~6+ weeks of history | **Drift layer** |
| E | **Gap / hazard model** (time between bookings) | Handles occasional users with no weekly rhythm | Weak on time and place | **Fallback for occasional users** |
| F | **Global supervised model** (LightGBM over user × candidate-slot rows) | Learns patterns shared across users; strong probabilities | Less readable; must prove it adds value | **Optional booster**, kept only if the ablation shows a gain |
| G | Sequence deep learning (LSTM/Transformer) | Powerful with big real data | Overkill, opaque, data-hungry | ✗ |
| H | LLM | — | Decided against | ✗ |

**Chosen: C → B (+ D, E) → optional F → calibration → policy.** Every stage is a separate, testable function, which is what makes the ablation table possible.

---

## 3. Engine design (stage by stage)

### 3.1 Ingest & clean
- Validate against a JSON Schema; accept CSV or JSON; column aliases are configurable (for GoPark's real data later).
- Timestamps → `Asia/Beirut`. Store UTC as well; reject or repair timestamps without a timezone, with a warning.
- **Messy-data handling** (the generator injects all of these): duplicate rows, status spelled inconsistently, missing end time (use the user's median duration), end before start, future bookings beyond the cutoff (dropped).
- **Status weights:** `completed 1.0`, `no_show 0.6`, `cancelled 0.4` (still shows intent). A cancel followed by a rebook within 24 h for the same slot counts as one intent. Each user's `cancel_rate` is kept as a feature.
- Derived: weekday, start minute, duration, ISO week, `is_holiday_week`.

### 3.2 Activity windows
- A user's **active weeks** run from their first booking to the cutoff, **minus** holiday weeks (Lebanese public holidays + AUB term breaks, from a config file) and **minus inactive gaps**.
- An inactive gap is any stretch longer than `max(21 days, 3 × the user's median gap)`.
- Without this, a summer break looks like "routine ended" and drags down every probability.

### 3.3 Routine discovery
1. Per user and location, cluster start times on a **circular 24 h clock** (DBSCAN, eps ≈ 45 min, min samples 2), weighting each point by its status weight.
2. Each time cluster gives candidate slots `(location, weekday, time window)`. The window is the circular median ± the spread (circular standard deviation, capped at 90 min).
3. Within a cluster, weekdays whose hits pass the habit test (§3.4) form the routine's **day set**. Labels are normalised: Mon–Fri → "Weekdays", 7 days → "Daily", Sat+Sun → "Weekends", otherwise "Mon / Wed / Fri".
4. A user can have **several routines** (e.g. "Weekdays 9 AM · BDD" + "Sat 11 AM · Hamra").

### 3.4 Habit vs coincidence (the core)
For each candidate slot *s* with `n` active weeks and decay-weighted hits `h`:

- **Bayesian recurrence:** `p_s ~ Beta(α₀ + h, β₀ + (n − h))`.
  - Decay weight per week is `0.5^(age_weeks / 4)`, a 4-week half-life tuned on validation.
  - `α₀, β₀` come from the **population at that location and weekday** (empirical Bayes, method of moments) and are scaled down so the prior is worth about 2 weeks of evidence.
  - This pulls sparse users toward what is typical instead of trusting 2 lucky hits.
- **Test against chance:** if this user's bookings landed on random weekdays at their own overall rate, how likely is a count this high on this weekday? (Binomial tail, `p0 = user's weekly booking rate / 7`.)
- **A slot is a habit when all of these hold:**
  - `P(p_s > 0.5) ≥ 0.8`
  - the chance test gives p < 0.05
  - at least 3 hits across at least 3 distinct weeks
  - circular time spread ≤ 60 min
- **Otherwise:** *occasional* (some signal, not a habit) or *noise*.

These thresholds are written in config and tuned on the validation split, never on test.

### 3.5 Drift: routines that change
Each routine gets a lifecycle state:

| State | Rule (on decay-weighted weekly hits) |
|---|---|
| **emerging** | ≥ 2 hits in the last 3 weeks and ≈ 0 before; posterior mean computed on recent weeks only |
| **active** | Passes the habit test and the recent rate is consistent with the long-run rate |
| **fading** | Recent 4-week rate significantly below the prior rate (two-window test, p < 0.1), but not zero |
| **ended** | No hits in the last *k* expected occurrences, where `(1 − p̂)^k < 0.05` |

A CUSUM over weekly hits gives the **change date**, used in reasons ("since Oct 6"). An emerging routine plus an ended one at the same time = **routine switch** detected.

### 3.6 Next-booking prediction
- **Candidates:** every (date, location, time window) implied by active or emerging routines over the next 14 days.
- **Rule score:** `posterior mean p_s × activity_factor`. The activity factor shrinks toward 0 as the time since the last booking passes 2× the user's typical gap (it uses the inactivity flag).
- **Occasional users** (no routine): gap/hazard model → `P(any booking in the next 7 days)` from the user's past gaps, shrunk to the population. Location = most frequent recent one. Time = "variable".
- **Optional LightGBM booster:** one row per (user, candidate slot, cutoff); label = booked at that location within ±90 min of the window centre.
  - Features: rule posterior, hits in the last 1/2/4 same weekdays, EWMA weekly frequency, days since last booking, cancel rate, time spread, routine state, location popularity, user tenure.
  - Trained on early rolling cutoffs; final score is `logistic(w1·logit(rule) + w2·logit(gbm))`, fitted on validation.
- **Output:** the earliest candidate with calibrated probability ≥ τ; if none, the highest-probability one, marked "below reminder threshold".

### 3.7 Calibration
Isotonic regression fitted on the **validation** cutoffs (never test), applied to all candidate scores. Reported with a reliability diagram, ECE (average gap between stated and actual hit rate) and Brier score, before and after calibration.

### 3.8 Reminder policy
- **Decision rule:** remind if `p·benefit > (1 − p)·annoyance`, i.e. `p > c/(b+c)`. Default τ is where validation **precision ≥ 0.8** (expected near 0.7). Both knobs are in config and shown as a slider on the dashboard.
- **Value tiers.** The highest-confidence users may book anyway, so a reminder adds little for them:

  | Tier | Who | Action |
  |---|---|---|
  | **Locked-in** | Active routine, p ≥ 0.9, long streak | Light touch or no reminder |
  | **At-risk** ⭐ | Fading routine or recent misses, p in [τ, 0.9) | **Best reminder target** (most likely to change behaviour) |
  | **Emerging** | New routine | Reminder that cements the habit |
  | **Occasional / inactive** | p < τ | No reminder; win-back campaign if inactive |
- **Timing:** if the data has `created_at`, remind at the user's median booking lead time + 30 min. Otherwise: 20:00 the evening before for slots before 11:00, and 2 h before for later slots.
- **Suppress** if the slot is already booked, or a reminder went out in the last 48 h.
- **Caveat on the slide:** true uplift needs an A/B test (next step). We estimate an upper bound: captured bookings among at-risk users.

### 3.9 Explanations (template-based, deterministic)
- *"Booked AUB on 9 of the last 10 Mondays, usually 07:55–08:10. Last booking 3 days ago."*
- *"Routine switch: Tue/Thu at LAU since Oct 6 replaced Mon/Wed at AUB."*
- *"Occasional user: 5 bookings in 12 weeks at different times. Not predictable enough to remind."*
- *"Inactive for 34 days (usual gap 3 days). Confidence lowered."*

### 3.10 Aggregate outputs (value for GoPark)
- **Lot demand forecast:** expected arrivals per lot × date × hour = sum of individual calibrated probabilities, plus a base rate for unpredictable users. Compared with actual held-out bookings (error per lot-hour).
- **Segments:** cluster users on pattern features → named segments ("MWF students", "weekday commuters", "weekend leisure", "occasional", "lapsed"), with counts per location.
- **Churn watch:** users who just turned fading or inactive, ranked by past frequency.

---

## 4. Outputs

### 4.1 Pattern table (one row per user × routine, plus one primary row per user)
```json
{
  "user_id": "U001",
  "pattern_id": "U001-P1",
  "is_primary": true,
  "pattern_type": "recurring",
  "state": "active",
  "days": ["Mon","Wed","Fri"],
  "label": "Mon / Wed / Fri",
  "time_window": {"center": "08:00", "spread_min": 12},
  "location_id": "AUB",
  "support": 23,
  "active_weeks": 10,
  "recurrence_prob": 0.91,
  "next_booking": {"at": "2026-11-02T08:00:00+02:00", "location_id": "AUB"},
  "confidence": 0.92,
  "reminder": {"recommend": true, "tier": "locked_in", "at": "2026-11-01T20:00:00+02:00"},
  "last_booking_date": "2026-10-30",
  "frequency_per_week": 2.8,
  "inactivity_detected": false,
  "cancel_rate": 0.04,
  "change_date": null,
  "reason": "Booked AUB on 9 of the last 10 Mondays ...",
  "run_id": "2026-11-01T00:00Z-r12",
  "model_version": "rhythm-1.0 (rules+gbm, calibrated)"
}
```
Export formats: `patterns.csv` (flat, primary rows by default, `--all-routines` for all), `patterns.json`, plus `forecast.csv`, `segments.csv`, `metrics.json`, `evaluation_report.html`.

---

## 5. Synthetic data generator

### 5.1 Personas (with ground truth)
| Persona | Behaviour | Tests |
|---|---|---|
| Weekday commuter | Mon–Fri ~9:00 @ BDD | Baseline routine |
| MWF student | Mon/Wed/Fri ~8:00 @ AUB, stops in term breaks | Day sets + holidays |
| Tue/Thu | Tue/Thu ~10:00 @ LAU | Day sets |
| Multi-routine | Weekdays @ office + Sat @ gym/Hamra | Several routines |
| **Routine switcher** | Switches days/location mid-period | Drift detection + delay |
| **Fader** | Routine thins out over weeks | Fading state |
| **Noisy timer** | Routine with ±40–60 min jitter | Time tolerance |
| **Heavy canceller** | 30–50% cancellations | Status weighting |
| Occasional | Random 2–6 bookings in 12 weeks | Habit vs coincidence |
| **Coincidence trap** | Random, but happens to hit Mondays 3–4 times | False-habit rate |
| New user | 1–3 weeks of history | Sparse handling |
| Lapsed | Strong routine then inactive | Inactivity / churn |
| *Hidden 1 & 2* | Defined secretly by the generator owner, revealed only at final evaluation | Overfitting check |

Per-user parameters (times, probabilities, lead times, durations) are **randomised** within each persona. Locations: AUB, BDD, LAU, Hamra, + 4–6 more for realism. Includes `created_at` (booking lead time per persona) so reminder timing can be learned, and a **messy-data injector** (§3.1).
Default size: **2,000 users × 26 weeks ≈ 150k bookings**. Scale set: 1M+ bookings by cloning users.

### 5.2 Independence protocol (answers "you built the test you pass")
- The generator owner (Person 1) writes it **without seeing engine code**; the engine owner never tunes on the test split.
- Hidden personas are revealed only for the final evaluation run, and their results are reported separately.
- All seeds and configs are published so judges can regenerate.

---

## 6. Evaluation

### 6.1 Splits (nested, time-based)
```
weeks 1 ─────────── 18 | 19 ── 22 | 23 ── 26
        train/history   validation   test (4 weekly rolling cutoffs)
                        (tune τ, calibrate, blend)
```

### 6.2 Metrics
| Group | Metric |
|---|---|
| Next booking | **Hit@1** (same day + location, start within ±60 min), day-only hit, location accuracy, time MAE (minutes) |
| Probabilities | Brier score, log loss, **ECE**, reliability diagram |
| Reminders | Precision, recall, reminders per user per week, at-risk capture |
| Pattern recovery (vs ground truth) | Day-set Jaccard, location accuracy, time error, user-type F1 (recurring vs occasional), **false-habit rate** on coincidence traps |
| Drift | Detection delay (weeks), false-alarm rate |
| Forecast | Error per lot-hour vs actual |
| Scale | Runtime and memory at 10k / 100k / 1M bookings |

### 6.3 Baselines to beat
- **B1:** repeat last week.
- **B2:** most frequent weekday + modal time + modal location.
- **B3:** frequency rules with share-as-confidence (approach A).

### 6.4 Ablation (shows each part earns its place)
Full engine vs −population prior, −decay, −drift, −activity windows, −LightGBM, −calibration.

### 6.5 Robustness curves
Hit@1 and ECE as a function of: weeks of history (2/4/8/12/18), time noise σ, cancel rate, share of messy rows.

---

## 7. System architecture

### 7.1 Components
```
┌───────────────────────────── Dashboard (React + Vite + TS, ECharts) ───────────────────────────┐
│ Overview · Pattern table · User drill-down · Evaluation · Lot forecast · Upload your CSV       │
└───────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                │ REST
┌───────────────────────────────────────────────▼────────────────────────────────────────────────┐
│ API (FastAPI)   /datasets  /runs  /patterns  /users/{id}  /evaluation  /forecast  /segments    │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ rhythm (Python package + CLI)                                                                  │
│   ingest · activity · discover · habit · drift · predict · booster · calibrate · policy ·      │
│   explain · aggregate · evaluate                                                               │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Storage: Parquet files per dataset/run (+ SQLite index of runs)  ·  config.yaml                │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
 tools/generator  (separate package, owned independently)
```

### 7.2 Stack
| Layer | Choice | Why |
|---|---|---|
| Engine | Python 3.12, pandas + numpy (vectorised), scipy, scikit-learn (DBSCAN, isotonic), LightGBM | Team knows Python/ML; fast enough for 1M rows |
| API | FastAPI + uvicorn | Typed, auto-generated docs (useful to show judges) |
| Storage | Parquet + SQLite | No database server; runs anywhere |
| Dashboard | **React + Vite + TypeScript + ECharts** ✅ decided | Team has JS skills; UX is 15% of the score |
| Packaging | `docker compose up` → generator + API + dashboard; also `pip install -e .` | One-command demo for judges |
| Tests | pytest with tiny hand-made personas (golden files) | Catches regressions during the rush |

### 7.3 CLI
```
rhythm generate  --users 2000 --weeks 26 --seed 42 --out data/bookings.csv
rhythm run       --input data/bookings.csv --cutoff 2026-11-01 --out runs/
rhythm evaluate  --input data/bookings.csv --truth data/truth.json --report runs/eval/
rhythm bench     --sizes 10k,100k,1m
```

### 7.4 API
```
POST /datasets                 upload CSV/JSON → dataset_id (+ validation report)
POST /runs                     {dataset_id, cutoff?, params?} → run_id
GET  /runs/{id}/patterns       ?format=csv|json &user= &state= &min_conf= &all_routines=
GET  /users/{id}               routines, history, heatmap, timeline, prediction, reason
GET  /runs/{id}/evaluation     metrics + chart data
GET  /runs/{id}/forecast       ?lot= &date=
GET  /runs/{id}/segments
```

### 7.5 Dashboard pages
1. **Overview:** KPIs (users, % recurring, reminders due tomorrow by tier, lapsed this week) + segment chart.
2. **Pattern table:** the brief's table, filterable, confidence bars, state badges, reason column, CSV/JSON export.
3. **User drill-down:** weekday × hour heatmap, booking timeline with routine bands and change-date markers, next booking + confidence + reminder time, reason.
4. **Evaluation:** ours vs baselines, reliability diagram (before/after calibration), precision-recall curve with a τ slider, ablation table, robustness curves, per-persona breakdown, hidden-persona results.
5. **Lot forecast:** predicted vs actual demand per lot and hour.
6. **Upload:** drop any CSV with the same columns → live run → table. Judges can try their own data.

### 7.6 Scalability story
- Per-user work is independent: grouped and vectorised, parallel across processes, and splits across machines the same way.
- Incremental mode: only users with new bookings since the last run are recomputed. The population prior is refreshed nightly.
- Measured runtime curve from `rhythm bench` goes on a slide.

---

## 8. Gaps the brief leaves open, and our answers

| Gap | Our answer |
|---|---|
| **Prediction ≠ uplift.** Reminding a locked-in user adds nothing. | Value tiers (§3.8); target at-risk users; recommend an A/B test as the next step |
| **No booking-creation time** | Generator includes `created_at`; engine uses it if present, falls back to heuristics; ask GoPark to export it |
| **Synthetic-only data** | Independence protocol, hidden personas, published seeds, upload page for real data |
| **Holidays & term breaks** | Calendar config; those weeks excluded from active weeks |
| **What "completed" means** (does it include no-shows?) | Status weights are configurable; documented assumption |
| **Nearby lots** (same user, two lots 200 m apart) | Optional location-group map in config; routines can be detected at the group level |
| **Cold start** | Population prior per location; "insufficient history" label below 3 weeks |
| **Time zone / daylight saving** | UTC storage + IANA `Asia/Beirut` |
| **Privacy** | Works on pseudonymous IDs only; no personal data needed; aggregates for forecast and segments |
| **Notification fatigue** | 48 h cap, suppression if already booked, annoyance cost in the decision rule |

---

## 9. Build plan

### 9.1 Team (4; 3-person fallback below)
| Person | Owns |
|---|---|
| **P1 — Data** | Generator, personas, messy-data injector, ground truth, hidden personas (works independently of P2) |
| **P2 — Engine** | ingest → activity → discover → habit → drift → predict → explain |
| **P3 — Eval/ML** | Backtest harness, baselines, calibration, reminder policy, LightGBM, ablation, robustness, benchmark, forecast, segments |
| **P4 — Product** | FastAPI, React dashboard, exports, upload flow, docker, slides, demo |

**3 people:** P1 finishes the generator by ~hour 10, then takes over the API and simple dashboard pages. P3 builds the evaluation page.

### 9.2 Phases (36–48 h)
| Phase | Hours | Goal | Done when |
|---|---|---|---|
| **P0** | 0–3 | Input + output JSON schemas, repo, generator v0 (3 personas), engine stub, dashboard skeleton | A thin end-to-end slice runs: CSV → table in the browser |
| **P1** | 3–12 | Generator v1 (all personas, messy data); engine v1 (discover + Bayesian habit); backtest harness + 3 baselines; table page + export | First metrics.json; engine beats B2 on Hit@1 |
| **P2** | 12–24 | Drift states, activity windows, occasional-user fallback, explanations; calibration; reminder policy + tiers; user drill-down page | Reliability chart looks calibrated; reasons on every row |
| **P3** | 24–32 | LightGBM booster + ablation; robustness curves; forecast + segments; evaluation page; hidden-persona run | Full evaluation page with real numbers |
| **P4** | 32–40 | Scale benchmark, upload flow, docker, polish, slides, 2 demo rehearsals, backup video | Clean dry runs |
| Buffer | 40–48 | Fixes, sleep | — |

**Cut order if behind:** segments → LightGBM (keep rules + calibration) → forecast page → upload flow → robustness curves.
**Never cut:** calibrated confidence, backtest vs baselines, reasons, CSV/JSON export, pattern table.

### 9.3 Repo layout
```
gopark-rhythm/
  rhythm/                 engine package (one module per stage) + cli.py
  api/                    FastAPI app
  dashboard/              React + Vite
  tools/generator/        personas, injector, truth export (owned by P1)
  config/                 config.yaml, holidays.yaml, locations.yaml
  data/  runs/  tests/  docs/  slides/
  docker-compose.yml
```

---

## 10. Risks
| Risk | Mitigation |
|---|---|
| Engine "overfits" the generator | Independence protocol + hidden personas |
| LightGBM gives no gain | Fine: the ablation shows it honestly, and rules + calibration stand alone |
| Too many knobs to tune in time | Defaults in config; tune only τ, decay half-life and DBSCAN eps on validation |
| Dashboard eats all the time | Build the table + drill-down first; evaluation charts use ready-made ECharts options produced by the API, so the UI only renders them |
| Demo machine issues | Docker + pre-computed runs + backup video |

---

## 11. Demo script (~5 min)
1. **The problem in one line**, then the pattern table straight away. Point at U001 (locked-in), a routine-switcher (state change + reason) and a coincidence trap correctly labelled *occasional*.
2. **Drill into the switcher:** heatmap, timeline with the change date, new prediction.
3. **"Can you trust the 92%?"** Show the reliability diagram before vs after calibration.
4. **Reminder slider:** move τ and watch precision and recall change; tiers show who's worth reminding.
5. **Evaluation:** beats 3 baselines; ablation; hidden personas; robustness curve.
6. **Value:** lot demand forecast vs actual; lapsed-user list.
7. **Scale + openness:** 1M bookings in N seconds; upload a fresh CSV live; export JSON.
8. **Next steps:** real data, A/B test of reminder uplift, `created_at` field, incremental production pipeline.

---

## 12. Decisions & remaining questions

| Decision | Value |
|---|---|
| Scope | **Challenge 2 only** |
| Time / team | 36–48 h, 3–4 people (RN/JS, Flutter, Python/ML, Node) |
| Data | Synthetic only, our own generator |
| LLM | None; template reasons |
| Language | English |
| Engine / API | Python + FastAPI |
| Dashboard | **React + Vite + TypeScript + ECharts** (decided 2026-10-31) |

**Still open:**
1. Presentation length, and is a live demo required?
2. Can GoPark tell us what its real status values are, and whether it records booking-creation time? (It shapes §3.1 and §3.8, but we can proceed with assumptions.)
