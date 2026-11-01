# GoPark × AUB Hackathon — Architecture Plan (Both Challenges)

> Working name: **GoPark Sense** · Predict → Sense → Confirm → Learn
> Status: **v3.0** · 2026-10-31 · Combined C1 + C2. Earlier versions are in `docs/archive/`.
> Constraints: 36–48 h · 3–4 people (+ optional hardware helper) · synthetic data · no LLM · English (RTL-ready)
> Stack: Expo (React Native) · FastAPI · Python engine · React + Vite + TS + ECharts dashboard

---

## 0. TL;DR

One closed-loop system. Each challenge works on its own, and each makes the other better.

```
            ┌──────────────────────────── LEARN ─────────────────────────────┐
            │  actual arrival/departure times, no-shows, overstays           │
            ▼                                                                │
  ┌──────────────────────┐  per-user arrival prior,   ┌───────────────────────┴──────┐
  │ C2  Rhythm engine     │  expected arrivals per lot,│ C1  Presence + Attendant     │
  │ bookings → routines,  │  reminder targets          │ signals → confident          │
  │ next booking,         │ ─────────────────────────▶ │ check-in/out, alerts,        │
  │ calibrated confidence │                            │ incentives, offline app      │
  └──────────────────────┘                            └──────────────────────────────┘
          PREDICT                                            SENSE → CONFIRM
```

- **C1, Sense → Confirm:** several weak signals (geofence, a "driving → walking" motion switch, BLE beacon, gate QR, NFC, plate scan) are combined into **one confidence score**.
  - High → **auto check-in/out**.
  - Medium → **one-tap driver confirmation** that earns points.
  - Low → **attendant task**.

  The attendant becomes an *exception handler*. The attendant app works **offline**, using **signed QR codes** that can be checked without a connection.
- **C2, Predict:** a rule/ML hybrid:
  - finds several routines per user;
  - separates habit from coincidence with a Bayesian test;
  - labels routines **emerging / active / fading / ended**;
  - predicts the next booking with **calibrated** confidence;
  - recommends reminders by **value tier**;
  - writes a plain-language reason for every row.

  It's proven by a time-based backtest against baselines, plus hidden test personas.
- **The links:**
  1. C2's prediction is the **starting probability** in C1's evidence fusion, so routine users get auto check-in with fewer signals.
  2. C2's per-lot **expected arrivals** feed the attendant's "expected soon" list.
  3. C1's real arrival and departure times feed back into C2 as better labels.

---

> **Final detection design (owner decision, 2026-11-01).** At each gate, **two presence sensors** (IR break-beam or ultrasonic on an ESP32, ~1 m apart) detect every car entering or leaving; trigger order gives direction. A **camera** facing the lane (old phone) reads the plate on entry and exit to identify the car. Fusion pairs `gate_sensor` + `camera_plate` events within ~±8 s into one identified check-in/out. A sensor event without a readable plate still updates occupancy and raises an "identity unknown" task. The driver's phone signals are backup; NFC/QR optional; BLE beacons and the rotating gate-QR screen are dropped. This supersedes the kit table in §3.3 where they differ.

## 1. Understanding the problem

### 1.1 Context that shapes the design
GoPark is a Lebanese parking reservation app (sample locations: AUB, BDD, LAU, Hamra). Beirut realities to plan for:
- patchy data and power cuts;
- **underground garages with no GPS or signal**;
- tall buildings that throw GPS off;
- **valet** lots;
- bilingual users;
- the 2023 daylight-saving split (store UTC, show `Asia/Beirut`).

### 1.2 Challenge 1, decoded
| Asked | Means |
|---|---|
| Parking overview | Live free/reserved/occupied per lot, and per spot where spots exist |
| Check-in/out | Validate the booking; record **actual** timestamps |
| Lookup | Plate, booking ID or QR, and it must work offline |
| Alerts | Upcoming arrivals, overstays, unconfirmed departures (+ no-shows, unknown vehicles) |
| History | Append-only audit log (who, what, when, which device, how) |
| Resilience | Wrong scans, missed events, intermittent connectivity |
| Innovation | Detect arrival/departure without relying fully on attendants; incentives; false detection, privacy, cost, different lot types |
| Deliverable | Working attendant workflow + detection mechanism + **accuracy, cost, limits, incentive design** |

### 1.3 Challenge 2, decoded
| Asked | Means |
|---|---|
| Input | User, booking ID, location, start, end, status. No creation time, no coordinates. |
| Output | Per-user table: pattern, time/place, next likely booking, confidence |
| Extras | Last booking, frequency, inactivity, **reminder timing**, **reason** |
| Hard questions | Habit vs coincidence; several routines; changing routines; sparse data; cancellations; inconsistent times; the confidence below which no reminder is sent |
| Deliverable | Engine + CSV/JSON/API + **held-out evaluation** |

### 1.4 Judging weights → levers
| Criterion | Weight | Lever |
|---|---|---|
| Innovation | 25% | Evidence fusion with a C2 starting probability; motion-switch detection; rotating gate QR; drift states; value-tiered reminders; the closed loop |
| Technical | 25% | Offline event-sourced sync, signed QR; Bayesian habit test, calibration, backtest + baselines + hidden personas |
| Feasibility | 20% | Software-first; cheap hardware kits per lot type; engine scales to 1M bookings |
| UX/clarity | 15% | ≤ 2-tap attendant flow; one-tap driver confirm; reasons on everything; clean dashboard |
| Value | 15% | Accurate occupancy, fewer attendant hours, reminder targets, demand forecast, inactive-user flags |

---

## 2. Strategy paths

| Path | Verdict |
|---|---|
| C1 deep, C2 thin | ✗ wastes the link and the AI story |
| C2 deep, C1 mock | ✗ C1 judges want a working workflow |
| Two separate projects | ✗ splits the team and the story |
| **Closed loop, each half with a standalone MVP** | **✓ chosen.** Scope is controlled by the cut lines in §10 |

---

## 3. Challenge 1: detection paths explored

| # | Mechanism | Arrival | Departure | Cost per lot | Main failure modes | Privacy |
|---|---|---|---|---|---|---|
| 1 | Booking QR, attendant scans | Very high | Very high | ~$0 | Needs an attendant; queues | Good |
| 2 | **Rotating gate QR**, driver scans | High | High if the driver bothers | $0–60 | Forgetting; a static QR can be photographed | Good |
| 3 | NFC sticker tap | High | Medium | <$1 per tag | Forgetting; iOS needs the app | Good |
| 4 | GPS geofence | Medium | **Low** (the phone leaves, not the car) | $0 | Drift near buildings, neighbouring lots, OS delay, permission refused, spoofing, **no GPS underground** | Medium |
| 5 | **Motion switch** (driving → walking inside the fence) | High with #4 | **High** (fixes #4's phone-vs-car gap) | $0 | Passengers, valet | Medium |
| 6 | BLE beacon | High | Medium-high | ~$10–25 each | Battery, signal reflections; **works underground** | Good |
| 7 | Car Bluetooth disconnect/reconnect | High | High | $0 | Not every car; native code needed | Good |
| 8 | Gate plate camera | Very high | Very high | ~$100–250 per lane | Lighting, angle; Lebanese plate format | Sensitive: process on device |
| 9 | Attendant phone plate scan | Very high | Very high | $0 | Needs a person, but faster than typing | Same as #8, no fixed camera |
| 10 | Per-spot sensors | Occupancy only | Occupancy only | ~$30–100 per spot | Doesn't identify the car | Good |

### 3.1 Chosen: evidence fusion with confidence tiers
```
logit P(arrived) = logit(prior) + Σ  log LR(signal_i) · freshness_i

prior ← a booking exists for this lot/time
      ← C2: P(user arrives at this lot around now)            ◀── link to C2
LR    ← per-signal likelihood ratios: geofence ≈ 4, motion switch ≈ 8, BLE ≈ 15,
        plate match ≈ 50, gate QR ≈ 200, attendant = definitive (starting values, tuned)
```
(Each likelihood ratio says how much more likely that signal is if the car really arrived. Adding them in log form is what makes the score explainable.)

| Fused P | Action | Who |
|---|---|---|
| ≥ 0.95 | **Auto check-in/out**, `method=auto`, reversible | Nobody |
| 0.60–0.95 | Push "Parked at AUB Lot B? Yes / No" → +points | Driver (one tap) |
| < 0.60 after 10 min | Task in the attendant's "Needs eyes" queue | Attendant |
| Contradiction (QR at lot X, GPS at lot Y) | Anomaly, never auto-confirmed | Attendant |

The attendant always sees *why*: "geofence 07:58 + parked-motion 08:01 + Monday 8 AM routine (92%)".

### 3.2 Departure (the harder half)
Evidence, strongest first:
1. gate camera or BLE exit;
2. walking → driving + leaving the fence;
3. car Bluetooth reconnect + leaving the fence;
4. driver confirmation;
5. arrival at another lot (the car can't be in two places);
6. booking end time passed with all signals silent → **inferred** departure, flagged and sent for a spot-check.

### 3.3 Deployment kits by lot type
| Lot type | Kit | Hardware cost | Demo |
|---|---|---|---|
| **Open-air** (AUB) | Geofence + motion + printed gate QR (+ NFC) | ~$0–5 | **Live (simulated) + field video** |
| **Underground** (BDD) | BLE beacon at the ramp + rotating QR + NFC; offline evidence buffer | ~$40–100 | **Live (simulated) + field video** |
| Gated (LAU, spot-level) | Gate plate camera + BLE | ~$150–300 per lane | Slide (+ Pi camera if time) |
| Valet (Hamra) | Attendant-led scan at handover; driver phone ignored for departure | ~$0 | Slide + attendant flow |

### 3.4 Incentives (points + perks + small discounts)
| Incentive | Mechanic |
|---|---|
| Points per confirmation | +10 arrival, +10 departure, streak multiplier |
| **Grace-time bank** | Each verified departure banks free grace minutes (capped) that absorb a future overstay |
| Loyalty reward | Every 10 confirmed stays = 1 h free at your usual lot |
| **Trust tier ("Verified")** | Hands-free auto check-in, priority on sold-out lots, pay-after |
| Spot hold | Confirmed "on my way" holds the spot past the start time |
| Routine nudge *(link to C2)* | "Book your usual Monday spot, +20 pts" for at-risk routines |
| Anti-fraud | Points need ≥ 2 independent signals or an attendant; daily cap; anomaly flags |

### 3.5 Attendant app
1. **Lot overview:** capacity bar, free/reserved/occupied, a spot grid where spots exist, an **"Expected in the next 30 min"** strip (bookings + C2 predictions).
2. **Scan:** one button; the camera reads a QR *or* a plate; manual entry fallback. Result: ✅ valid / ⚠ wrong lot / ⚠ early / ⚠ expired / ❌ not found → register walk-in.
3. **Needs-eyes queue:** low-confidence events, unconfirmed departures, overstays, plate mismatches. Swipe to resolve.
4. **Alerts:** upcoming arrivals, overstays (with estimated fee; payment out of scope), unconfirmed departures, likely no-shows.
5. **History:** every change with actor, method (auto/driver/attendant), device, synced/pending badge.
6. **Sync indicator:** online / offline with N pending. Never blocks the attendant.

### 3.6 Driver mode (same Expo app)
Booking QR · "on my way" · one-tap confirm prompts · points / grace bank / tier · background signals (geofence, motion, BLE, NFC) that queue offline and fill in later.

### 3.7 Resilience
| Failure | Handling |
|---|---|
| Attendant offline | Today's bookings cached; **Ed25519-signed QR** checked offline with a public key; local outbox |
| Double scan | Client-generated UUIDv7 per action → the server ignores duplicates |
| Conflicting offline actions | Event log ordered by `observed_at` + device sequence; impossible orderings go to an anomaly queue |
| Wrong lot / time / plate | Explicit result; attendant can override, with a reason logged |
| Unknown vehicle | Walk-in registration |
| Driver offline underground | Evidence stored with original timestamps and filled in later |
| Missed check-out | Inferred departure + alert |
| Clock skew | Server keeps `observed_at` + `received_at`; corrects using a sync-time offset |

---

## 4. Challenge 2: Rhythm engine

### 4.1 Paths explored
| Approach | Role |
|---|---|
| Frequency rules (share = confidence) | **Baseline B3** |
| **Bayesian recurrence** (Beta-Binomial, time decay, population prior) | **Core scorer** |
| **Clustering** (DBSCAN on a circular clock × location) | **Routine discovery** |
| **Change-point** (two-window test + CUSUM) | **Drift layer** |
| Gap/hazard model | **Occasional-user fallback** |
| LightGBM global model | **Stretch booster**, kept only if the ablation shows a gain |
| Deep sequence models, LLM | ✗ |

### 4.2 Pipeline
```
bookings ─▶ Clean ─▶ Active weeks ─▶ Discover ─▶ Habit test ─▶ Drift ─▶ Predict ─▶ Calibrate ─▶ Remind ─▶ Explain
                                                                                                    │
                                     pattern table · arrival priors (→C1) · lot forecast (→C1) · CSV/JSON/API
```

1. **Clean:**
   - JSON Schema validation; CSV or JSON; times converted to `Asia/Beirut`.
   - Repairs: duplicates, inconsistent status spelling, missing end time, end before start.
   - Status weights: completed 1.0 · no_show 0.6 · cancelled 0.4. A cancel + rebook within 24 h counts as one booking intent.
2. **Active weeks:** from the first booking to the cutoff, **minus** holiday and term-break weeks (`holidays.yaml`) and inactive gaps longer than `max(21 d, 3 × the user's median gap)`.
3. **Discover:** per user and location, DBSCAN on start times on a circular clock (eps ≈ 45 min). Each time cluster's weekdays become candidate routines, named "Weekdays", "Daily", "Mon / Wed / Fri"… Several routines per user are allowed.
4. **Habit test:** `p ~ Beta(α₀ + h, β₀ + n − h)`.
   - Decay half-life is 4 weeks. The prior comes from the population at the same location and weekday (empirical Bayes), worth about 2 weeks of evidence.
   - **Habit if** `P(p > 0.5) ≥ 0.8` **and** a binomial test against the user's own random-weekday rate gives p < 0.05 **and** ≥ 3 hits across ≥ 3 weeks **and** time spread ≤ 60 min.
5. **Drift:** each routine is labelled `emerging | active | fading | ended`, with a change date from CUSUM. Emerging + ended at once = **routine switch**.
6. **Predict:** candidates over the next 14 days, `score = posterior mean × activity factor` (the factor shrinks when the user has been away longer than 2× their typical gap). Occasional users get a gap model ("likely within 7 days at Hamra, ~40%").
7. **Calibrate:** isotonic regression on the **validation** weeks only; reliability diagram, ECE and Brier score before and after.
8. **Remind:** remind if `p > c/(b+c)` (benefit of a captured booking vs the annoyance cost of a wasted one). Default τ is where validation precision ≥ 0.8. Tiers:

   | Tier | Who | Action |
   |---|---|---|
   | Locked-in | p ≥ 0.9, long streak | Light touch / none |
   | **At-risk** ⭐ | Fading routine or recent misses, p in [τ, 0.9) | **Best target** |
   | Emerging | New routine | Cement the habit |
   | Occasional / inactive | p < τ | None, or a win-back campaign |

   Timing: median booking lead time + 30 min if `created_at` exists; otherwise 20:00 the evening before for morning slots, or 2 h before. Suppress if already booked or reminded in the last 48 h.
9. **Explain** (templates): *"Booked AUB on 9 of the last 10 Mondays, usually 07:55–08:10."* · *"Routine switch: Tue/Thu at LAU since Oct 6."* · *"Inactive 34 days (usual gap 3); confidence lowered."*

### 4.3 Outputs
**Pattern table** (one primary row per user + one row per routine):
```json
{
  "user_id": "U001", "pattern_id": "U001-P1", "is_primary": true,
  "pattern_type": "recurring", "state": "active",
  "days": ["Mon","Wed","Fri"], "label": "Mon / Wed / Fri",
  "time_window": {"center": "08:00", "spread_min": 12}, "location_id": "AUB",
  "support": 23, "active_weeks": 10, "recurrence_prob": 0.91,
  "next_booking": {"at": "2026-11-02T08:00:00+02:00", "location_id": "AUB"},
  "confidence": 0.92,
  "reminder": {"recommend": true, "tier": "locked_in", "at": "2026-11-01T20:00:00+02:00"},
  "last_booking_date": "2026-10-30", "frequency_per_week": 2.8,
  "inactivity_detected": false, "cancel_rate": 0.04, "change_date": null,
  "reason": "Booked AUB on 9 of the last 10 Mondays ...",
  "run_id": "r12", "model_version": "rhythm-1.0"
}
```
**Feeds to C1:**
- `arrival_priors`: (user, lot, date, time window, p), used as the fusion prior.
- `lot_forecast`: expected arrivals per lot per hour (sum of calibrated probabilities + a base rate for unpredictable users), shown in the attendant's "expected soon" strip and the dashboard.

Exports: `patterns.csv/json`, `forecast.csv`, `metrics.json`, `evaluation_report.html`.

### 4.4 Evaluation
- **Splits (by date):** weeks 1–18 history · 19–22 validation (tune τ, calibrate) · 23–26 test (4 weekly rolling cutoffs).
- **Metrics:**
  - **Hit@1**: same day + location, start within ±60 min.
  - Time error (MAE); Brier, ECE, reliability diagram.
  - Reminder precision and recall.
  - Pattern recovery vs ground truth (day-set overlap, false-habit rate on coincidence traps).
  - Drift detection delay; forecast error per lot-hour.
- **Baselines:** B1 repeat last week · B2 modal weekday/time/location · B3 frequency rules.
- **Ablation:** full engine vs −prior, −decay, −drift, −active weeks, −calibration (−LightGBM if built).
- **Stretch:** robustness curves (accuracy vs weeks of history and time noise), 1M-booking benchmark.

---

## 5. Shared synthetic data generator (feeds both challenges)

### 5.1 Personas (with ground truth)
| Persona | Behaviour | Tests |
|---|---|---|
| Weekday commuter | Mon–Fri ~9:00 @ BDD | Baseline routine |
| MWF student | Mon/Wed/Fri ~8:00 @ AUB, stops in term breaks | Day sets, holidays |
| Tue/Thu | ~10:00 @ LAU | Day sets |
| Multi-routine | Weekdays @ office + Sat @ Hamra | Several routines |
| **Routine switcher** | Changes days/location mid-period | Drift |
| **Fader** | Routine thins out | Fading state |
| Noisy timer | ±40–60 min jitter | Time tolerance |
| Heavy canceller | 30–50% cancels | Status weights |
| Occasional | 2–6 random bookings in 12 weeks | Habit vs coincidence |
| **Coincidence trap** | Random, but hits Mondays 3–4 times | False-habit rate |
| New user · Lapsed | Sparse · strong then inactive | Cold start · inactivity |
| *Hidden 1 & 2* | Secret, revealed only at final evaluation | Overfitting check |

### 5.2 What it outputs
- `bookings.csv`: ~2,000 users × 26 weeks ≈ 150k rows, with `created_at` and plate. Seeded, with messy rows injected.
- `truth.json`: each user's real routines and change dates.
- **`stays.csv`**: *actual* arrival/departure per completed booking (early/late arrivals, overstays, no-shows). This is C1 ground truth, and the C1→C2 learning link.
- **`traces/`**: per-trip driver signal timelines (GPS points, motion states, beacon sightings, phone battery/offline gaps) for the C1 simulator, including noisy and contradictory cases.
- Lots: AUB (open-air), BDD (underground), LAU (gated, spot-level), Hamra (valet), + 4–6 more.

### 5.3 Independence protocol
The generator owner doesn't see engine code. The engine is tuned only on validation. Hidden personas are revealed at the end. Seeds and configs are published.

---

## 6. How the challenges connect

| Direction | Hook | Must-have / stretch |
|---|---|---|
| C2 → C1 | `arrival_priors` as the fusion prior | **Must-have** (it's the story) |
| C2 → C1 | `lot_forecast` → "Expected in the next 30 min" | **Must-have** |
| C2 → C1 | Reminder tiers → "book your usual spot +20 pts" nudge | Slide + driver-app card |
| C2 → C1 | Per-user no-show and overstay risk → smarter alerts | Stretch |
| C1 → C2 | Actual stays (from `stays.csv`, later live) as labels; actual vs booked time used as a feature | Stretch (show on a slide as the learning loop) |
| C1 → C2 | Confirmation reliability score as a reminder-targeting feature | Stretch |

---

## 7. System architecture

### 7.1 Components
```
┌──────────────────────────────── Clients ────────────────────────────────┐
│ Expo app (iOS + Android)                Dashboard (React + Vite + TS)   │
│  ├─ Attendant mode (offline-first)       ├─ Lot live view + forecast    │
│  └─ Driver mode (signals, confirm,       ├─ Pattern table + drill-down  │
│     points, QR)                          ├─ C2 evaluation               │
│                                          └─ C1 detection log + accuracy │
└─────────┬──────────────────────────────────────────────┬────────────────┘
          │ REST + WebSocket                             │ REST
┌─────────▼──────────────────────────────────────────────▼────────────────┐
│ FastAPI service                                                         │
│  ops/    bookings adapter (mock) · sync · fusion · alerts scheduler ·   │
│          incentives ledger · lots overview                              │
│  rhythm/ runs · patterns · priors · forecast · evaluation               │
├─────────────────────────────────────────────────────────────────────────┤
│ rhythm (Python package + CLI): ingest · activity · discover · habit ·   │
│        drift · predict · calibrate · policy · explain · aggregate · eval│
├─────────────────────────────────────────────────────────────────────────┤
│ SQLite (ops data, event log)  ·  Parquet (C2 runs)  ·  config/*.yaml    │
└─────────────────────────────────────────────────────────────────────────┘
 tools/generator  ·  tools/simulator (replays traces → POST /signals)
 edge/: BLE beacons · NFC tags · ESP32 demo gate (servo barrier + LEDs, lifts on check-in) · Pi gate camera (stretch)
 Shopping list + human-only tasks: docs/hardware-kit.html
```

### 7.2 Stack
| Layer | Choice |
|---|---|
| Mobile | Expo / React Native (TypeScript), `expo-sqlite` outbox, `expo-camera` (QR), `expo-location` + task manager (geofence). BLE/NFC need a **development build** (`react-native-ble-plx`, `react-native-nfc-manager`); the attendant app runs in plain Expo Go |
| API | FastAPI + uvicorn, WebSocket for live updates |
| Engine | Python 3.12, pandas/numpy, scipy, scikit-learn, (LightGBM stretch) |
| Storage | SQLite + Parquet (no DB server) |
| Dashboard | React + Vite + TypeScript + ECharts |
| Packaging | `docker compose up` for API + dashboard + seeded data; Expo app via QR/dev build |
| Tests | pytest golden personas; a few API contract tests |

### 7.3 Data model (ops)
```
users(id, name, reliability_score, tier, points)
vehicles(id, user_id, plate_norm, plate_display)
lots(id, name, kind[open|underground|gated|valet], geo_polygon, capacity, beacon_uuid, gate_secret)
spaces(id, lot_id, label)                                   -- optional, spot-level lots only
bookings(id, user_id, vehicle_id, lot_id, space_id?, start_at, end_at, status, created_at)
signals(id, client_event_id, user_id?, device_id, lot_id, source, kind, observed_at, received_at, payload)
stays(id, booking_id?, lot_id, plate_norm, state, in_at, in_method, in_conf, out_at, out_method, out_conf, inferred)
audit_log(id, client_event_id UNIQUE, actor_type, actor_id, action, entity, before, after, observed_at, received_at, device_id)
alerts(id, type, lot_id, stay_id?, booking_id?, severity, status, created_at, resolved_by, resolved_at)
points_ledger(id, user_id, delta, reason, ref_event_id)
```
C2 tables (Parquet per run): `patterns`, `arrival_priors`, `lot_forecast`, `metrics`.

### 7.4 Stay state machine
```
BOOKED ─(T-30m)─▶ EXPECTED ─(weak signal)─▶ ARRIVING ─(P≥.95 | confirm | scan)─▶ CHECKED_IN
  │                  └──(end passed, no evidence)──▶ NO_SHOW                       │
  └─(cancel)─▶ CANCELLED                                     (now > end+grace) ──▶ OVERSTAY
                    CHECKED_OUT ◀──(P≥.95 | confirm | scan)───────────────────────┤
                         ▲                                (end passed, silent) ──▶ UNCONFIRMED_DEPARTURE
                         └──────────────(inferred / attendant)─────────────────────┘
```

### 7.5 API
```
# ops (C1)
POST /sync/push   {events:[…]} → {acks, conflicts}        GET /sync/pull?lot=&since=
POST /signals     driver evidence                         POST /scan {qr|plate|booking_id, lot_id}
POST /stays/{id}/confirm                                  GET  /lots/{id}/overview
GET  /alerts?lot=                                          GET  /users/{id}/rewards
WS   /live?lot=   stay/alert updates

# rhythm (C2)
POST /datasets    upload CSV/JSON                         POST /runs {dataset_id, cutoff?}
GET  /runs/{id}/patterns?format=csv|json&user=&state=&min_conf=
GET  /users/{id}/rhythm   heatmap, timeline, routines, prediction, reason
GET  /runs/{id}/priors?lot=&date=                         GET /runs/{id}/forecast?lot=&date=
GET  /runs/{id}/evaluation
```

### 7.6 Security & privacy
- QR = booking fields + Ed25519 signature. The gate code is `HMAC(secret, 30 s window)`, accepted within ±1 window.
- Location: **events only, no continuous tracking**; opt-in with a "why" screen; raw signals deleted after 30 days.
- Plates are read on the device and stored normalised + hashed; images never leave the device.
- C2 runs on pseudonymous IDs; forecasts are aggregates. Aligned with Lebanon's Law 81/2018 and data minimisation.
- Role-based access: an attendant sees only their own lot.

---

## 8. Dashboard (React + Vite)
1. **Live lots:** occupancy per lot, stays with method badges (auto/driver/attendant), alerts, expected-arrivals curve (forecast vs actual).
2. **Detection log:** each stay's evidence timeline and fused-confidence curve. This is how judges *see* the fusion work.
3. **Pattern table:** filters, confidence bars, state badges, reasons, CSV/JSON export.
4. **User drill-down:** weekday × hour heatmap, timeline with routine bands and change dates, next prediction, reminder tier.
5. **Evaluation:** C2 (ours vs baselines, reliability diagram, τ slider with precision/recall, ablation, hidden personas) + C1 (auto-confirm rate, false-confirm rate, time to confirm, per lot type, from the simulator + field test).

---

## 9. Gaps the brief leaves open, and our answers

| Gap | Answer |
|---|---|
| **Phone ≠ car** (passengers, valet, dead phone) | Motion switch + car Bluetooth; only the booking owner's device counts; valet uses an attendant-led kit |
| **Underground: no GPS or signal** | BLE + QR/NFC; offline evidence buffer |
| **Prediction ≠ reminder uplift** | Value tiers target at-risk users; A/B test as the next step |
| **No booking-creation time in C2** | Generator includes it; engine falls back to heuristics; ask GoPark to export it |
| **Synthetic-only data** | Independence protocol, hidden personas, published seeds |
| Walk-ins / non-GoPark cars | Walk-in registration keeps occupancy right |
| Spot vs lot level | Both supported; the spot grid appears only where spaces exist |
| Several vehicles, plate changes | Plate → vehicle → user mapping; a mismatch is a warning, not a rejection |
| Incentive fraud | Points need corroborated evidence; caps; anomaly flags |
| Holidays & term breaks | Calendar config, used by both C2 (active weeks) and C1 (expected arrivals) |
| Cold start | Population priors per location |
| DST / clocks | UTC + `Asia/Beirut`; device clock offset correction |
| Attendant adoption | ≤ 2 taps, works offline, sound/haptic feedback, RTL-ready |
| Location permission refused | Falls back gracefully to QR/NFC + attendant; incentives for opting in |

---

## 10. Build plan

### 10.1 Team (4 + optional hardware helper); one Claude Code session per person
| Person | Owns | Folders |
|---|---|---|
| **P1 — Mobile** | Attendant mode (overview, scan, queue, alerts, history, offline outbox, signed QR); driver mode (QR, confirm, rewards) | `apps/mobile/` |
| **P2 — Ops backend** | FastAPI ops: sync, stays state machine, fusion, alerts scheduler, incentives, WebSocket; trip simulator | `api/ops/`, `tools/simulator/` |
| **P3 — Rhythm engine** | C2 pipeline, calibration, reminders, priors + forecast feeds, evaluation, baselines, ablation | `rhythm/`, `api/rhythm/` |
| **P4 — Data + Dashboard** | Generator (independent of P3), personas, traces, stays; React dashboard; docker; slides | `tools/generator/`, `dashboard/` |
| *H — Hardware (optional)* | Dev builds on phones, BLE/NFC setup, BLE/NFC hooks in the driver app with P1, field test + video, Pi camera (stretch) | `edge/` |

**3 people:** P4's dashboard goes to P2 after hour 24, and the generator goes to P3, with the independence protocol relaxed: hidden personas are written by P2.
**No hardware helper:** P1 does dev builds + BLE/NFC in P3; field test is cut first after the Pi camera.

### 10.2 Running parallel Claude Code sessions
- **P0 contract first** (one person or one Claude, ~3 h): repo, `CONTRACT.md` (DB schema, API, JSON schemas for patterns/priors/forecast/signals/traces), seed data, stubs. Push to GitHub before anyone starts.
- Each person works on their own branch and **only in their own folders**. Changes to the contract go through a PR approved by P2 (API) or P3 (C2 schemas).
- Merge to `main` every ~4 h; P2 is the integration owner and runs the end-to-end smoke test after each merge.
- Each Claude session gets a brief: its section of this plan + `CONTRACT.md` + its folder list + its phase checklist.

### 10.3 Phases (36–48 h)
| Phase | Hours | P1 Mobile | P2 Ops backend | P3 Rhythm | P4 Data + Dash | H Hardware |
|---|---|---|---|---|---|---|
| **P0** | 0–3 | — | **Contract, repo, stubs** (with P3) | Contract (C2 schemas) | Generator v0 (3 personas) | Charge, unbox, set IDs on beacons; Apple/Mac check |
| **P1** | 3–12 | Overview, scan QR, check-in/out, history | Bookings, stays, audit, sync push/pull, scan validation | Clean, active weeks, discover, habit test → first table | Generator v1 (all personas, messy rows, stays, traces); dashboard skeleton + live lots | Dev builds (Android + iOS); NFC tags written |
| **P2** | 12–24 | Offline outbox + signed QR; alerts; needs-eyes queue | Fusion + simulator; alerts scheduler; WebSocket | Drift, predict, calibrate, remind, explain; backtest + baselines | Pattern table + drill-down; detection log page | Beacon placement + signal checks; test BLE/NFC hooks with P1 |
| **P3** | 24–32 | Driver mode: confirm, rewards, nudge card | **Link: priors → fusion**; incentives ledger | **Priors + forecast feeds**; ablation; hidden-persona run | Evaluation page (C2 + C1); forecast chart | Integration test on phones |
| **P4** | 32–40 | Polish, RTL-ready pass | End-to-end smoke tests, demo seed state | Metrics freeze, report export | Slides, docker, backup video | **Field test + video**, measured numbers |
| Buffer | 40–48 | Fixes, rehearsals ×2, sleep | | | | |

### 10.4 Cut lines
**Cut first → last:** Pi camera · ESP32 demo gate · LightGBM · plate OCR · robustness curves / 1M benchmark · C1→C2 live labels · real BLE/NFC (simulate instead) · field-test video · driver confirm UI (keep auto + attendant).
**Never cut:**
- attendant scan + offline outbox;
- alerts;
- simulator-driven auto check-in;
- pattern table + export;
- calibrated confidence + backtest vs baselines;
- **the C2 → C1 prior link and the "expected soon" strip.**

### 10.5 Repo layout
```
gopark-sense/
  CONTRACT.md  ARCHITECTURE_PLAN.md  docker-compose.yml
  apps/mobile/            Expo app (attendant + driver)
  api/ops/  api/rhythm/   FastAPI routers
  rhythm/                 C2 engine package + CLI
  dashboard/              React + Vite + TS
  tools/generator/        bookings, truth, stays, traces
  tools/simulator/        trace replayer → /signals
  edge/                   beacon configs, NFC payloads, (Pi camera)
  config/                 lots.yaml, holidays.yaml, fusion.yaml, rhythm.yaml
  data/  runs/  tests/  docs/  slides/
```

---

## 11. Risks
| Risk | Mitigation |
|---|---|
| Background location/BLE is hard in Expo within the time | Simulator replays traces into the same `/signals` endpoint; real sensing is a bonus |
| No Mac or Apple account for the iOS dev build | Driver demo on Android; iPhone runs attendant mode in Expo Go |
| Scope across two challenges | Cut lines; each half has a standalone MVP by hour 24 |
| Integration drift between 4 parallel builders | Contract-first, folder ownership, merges every 4 h, smoke test |
| Engine overfits the generator | Independence protocol + hidden personas |
| Demo network failure | Laptop hotspot, docker, pre-seeded state, backup video |

---

## 12. Demo script (~6 min)
1. **Sunday night (dashboard):** Rhythm has run. U001 "Mon/Wed/Fri 8 AM · AUB · 92%" with a reason; a routine switcher; a coincidence trap correctly marked *occasional*. Export JSON.
2. **Monday 07:55 (attendant app, AUB):** "Expected soon" shows U001 + the lot forecast curve.
3. **Simulator:** U001's trip: geofence → driving→walking → with the 92% prior, P = 0.98 → **auto check-in** appears live with the evidence trail; +10 pts.
4. **Weak signals** (GPS drift, no routine) → one-tap confirm → confirmed.
5. **Underground BDD:** beacon + NFC evidence filled in after reconnecting.
6. **Dead phone:** attendant scans the booking QR **in airplane mode** → validates offline → syncs on reconnect.
7. **Edge cases:** wrong-lot QR warning; unknown plate → walk-in; fast-forward → overstay alert + inferred departure.
8. **Proof:** C2 reliability diagram + baselines + hidden personas; C1 auto-confirm/false-confirm rates + field-test clip.
9. **Value & cost:** kit costs per lot type, attendant hours saved, reminder tiers, next steps.

---

## 13. Decisions & open questions

| Decision | Value |
|---|---|
| Scope | **Both challenges, closed loop** |
| Time / team | 36–48 h, 3–4 people (+ optional hardware helper), one Claude Code session each |
| Data | Synthetic only (shared generator) |
| LLM | None |
| Language | English, RTL-ready |
| Mobile / API / engine | Expo · FastAPI · Python |
| Dashboard | React + Vite + TS + ECharts |
| Hardware | Android + iPhone, Pi/webcam, BLE beacons, NFC tags |
| Detection demo | Simulated live + recorded field test |
| Live lot types | Open-air (AUB) + underground (BDD); gated + valet on slides |
| Spots | Lot- and spot-level both supported |
| Incentives | Points + perks + small discounts |
| Payment | Out of scope; overstay fee shown only |

**Still open:**
1. Presentation length, and is a live demo required?
2. Does anyone have a **Mac or Apple developer account** (needed for the iPhone development build)?
3. Will there be a hardware helper, or does P1 cover hardware?
4. Any GoPark detail on real status values and booking-creation time?
