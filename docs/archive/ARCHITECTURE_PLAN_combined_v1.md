# GoPark × AUB Hackathon — Architecture Plan

> Working name: **GoPark Sense** (Predict → Sense → Confirm → Learn)
> Status: v1.0 · 2026-10-31 · Decisions locked in §11 (combined submission · 36–48 h · 3–4 people · Expo + FastAPI · synthetic data · no LLM)

---

## 0. TL;DR

We build **one closed-loop system** that covers both challenges and makes each one stronger:

```
            ┌──────────────────────── LEARN ─────────────────────────┐
            │  actual arrival/departure times, no-shows, overstays   │
            ▼                                                        │
  ┌───────────────────┐  predicted arrivals,    ┌─────────────────────┴─────┐
  │ C2 Pattern Engine │  per-user priors,       │ C1 Presence & Attendant   │
  │ (bookings → user  │ ──────────────────────▶ │ (signals → confident      │
  │  routines + next  │  no-show/overstay risk  │  check-in/out, alerts,    │
  │  booking + conf.) │                         │  incentives)              │
  └───────────────────┘                         └───────────────────────────┘
        PREDICT                                     SENSE → CONFIRM
```

- **Challenge 1**: a **multi-signal presence engine** (geofence + motion "car→walking" switch + BLE/QR/NFC + optional plate camera) combines evidence into one confidence score. High confidence → **auto check-in**; medium → **one-tap driver confirmation** (earns points); low → **attendant task**. The attendant only handles the cases the system can't settle. The attendant app is **offline-first**, using **signed QR codes that can be checked offline**.
- **Challenge 2**: a **rule/ML hybrid**. Clustering finds candidate routines; Bayesian recurrence scoring (with decay) separates habit from coincidence and tracks changing routines; an optional global gradient-boosted model scores the next booking; calibrated confidence drives a **reminder policy**; every prediction carries a written reason. Exports to CSV, JSON and REST. Evaluated with a time-based backtest against baselines.
- **The link between them**: C2's prediction becomes the *prior* in C1's evidence fusion ("Rami usually arrives at AUB around 8:00 on Mondays"). That lets C1 auto-confirm sooner with fewer signals. C1's ground-truth timestamps then become better training labels for C2.

---

## 1. Understanding the problem

### 1.1 Who GoPark is (context that shapes the design)
- A Lebanese parking reservation and payment app. The sample locations (AUB, BDD, LAU, Hamra) are dense Beirut areas.
- Beirut realities the design must survive: **patchy mobile data and power cuts**, **underground garages with no GPS or signal**, tall buildings that degrade GPS accuracy, **valet culture** in many lots, Arabic and English users, and an infamous 2023 daylight-saving split (store UTC, render `Asia/Beirut`).

### 1.2 Challenge 1 — what is actually being asked
| Asked | What it really means |
|---|---|
| Parking overview | Live counts and a map of available, reserved and occupied places per lot (and per spot if spots exist) |
| Check-in/out | Validate a booking and record **actual** timestamps, which differ from booked times |
| Lookup | By plate, booking ID or QR, and it must work offline |
| Alerts | Upcoming arrivals, overstays, unconfirmed departures (+ no-shows, unknown vehicles) |
| Activity history | An append-only audit log of who changed what, when and from which device |
| Resilience | Wrong scans, missed events, intermittent connectivity |
| **Innovation** | Know arrival/departure **without relying fully on attendants**; incentives; handle false detection, privacy, cost and deployment variety |
| Deliverable | Working attendant workflow + a working or proposed detection mechanism + **accuracy, cost, limitations and incentive write-up** |

### 1.3 Challenge 2 — what is actually being asked
| Asked | What it really means |
|---|---|
| Input | Booking records only: user, booking ID, location, start, end, status |
| Output | A per-user pattern table: pattern, time/place, next likely booking, confidence |
| Recommended extras | Last booking date, frequency, inactivity flag, **reminder timing**, **written reason** |
| Hard questions | Habit vs coincidence; several patterns per user; changing routines; sparse histories; cancellations; inconsistent times; the confidence level below which no reminder is sent |
| Deliverable | Engine + CSV/JSON/API export + **evaluation against held-out history** |

### 1.4 Judging weights → design priorities
| Criterion | Weight | How this plan scores |
|---|---|---|
| Innovation | 25% | Evidence fusion with a pattern-derived prior; "attendant handles only exceptions"; motion-state detection; rotating gate QR; closed loop between C1 and C2 |
| Technical | 25% | Offline-first event-sourced sync, signed QR, Bayesian plus ML hybrid, calibrated confidence, backtest |
| Feasibility | 20% | Software-first; hardware optional and cheap; tiered deployment kits per lot type |
| UX/clarity | 15% | Big-button attendant app, one-tap driver confirmation, explanation text on every prediction |
| Value | 15% | Accurate occupancy → more sellable spots; reminders → more bookings; fewer attendant hours |

---

## 2. Strategy-level paths (how much of each challenge)

| Path | Description | Pros | Cons | Verdict |
|---|---|---|---|---|
| A. C1-heavy | Deep attendant app + detection; C2 is a thin script | Strong mobile demo | Leaves the obvious cross-challenge value unused; the AI story is weak | ✗ |
| B. C2-heavy | Deep ML engine + dashboard; C1 is a mockup | Easy to evaluate rigorously | C1 judges want a *working* workflow | ✗ |
| C. Two separate projects | Build both independently | Clear scope per challenge | Splits the team, no shared story, double the demo surface | ✗ |
| **D. Closed loop (recommended)** | Shared backend; each challenge has its own MVP; integration hooks connect them | One story; each half also stands alone for judging; shows real platform value | Scope risk, managed with cut lines (§9) | **✓** |

> ✅ Decided: combined submissions are allowed, so we build D with both halves weighted equally.

---

## 3. Challenge 1 — solution paths for detection

### 3.1 Options explored
| # | Mechanism | How it works | Arrival | Departure | Cost / lot | Main failure modes | Privacy |
|---|---|---|---|---|---|---|---|
| 1 | **Booking QR, attendant scans** | Driver shows a signed QR; attendant scans it | Very high | Very high | ~$0 | Needs an attendant; queues | Good |
| 2 | **Rotating gate QR, driver scans** | Cheap screen at the gate shows a code that changes every 30 s; driver app scans it | High | High (if the driver bothers) | $0 (printed static) to ~$60 (old tablet) | Driver forgets; a printed static QR can be photographed and reused | Good |
| 3 | **NFC sticker tap** | Driver taps phone on a sticker at the entrance or spot | High | Medium | <$1 per tag | iOS needs the app or App Clip; forgetting | Good |
| 4 | **GPS geofence** | OS fires an event on entering or leaving the lot area | Medium | **Low**: the *phone* leaves, not necessarily the car | $0 | Urban-canyon drift, neighbouring lots, OS delay, background-permission refusal, spoofing, **no GPS underground** | Medium |
| 5 | **Motion state** (OS activity recognition) | In-vehicle → walking inside the geofence = *parked*; walking → in-vehicle, then leaving the fence = *departed* | High (with #4) | **High** (fixes #4's car-vs-phone gap) | $0 | Passenger phones, valet, platform API limits | Medium |
| 6 | **BLE beacon** | Beacon at the gate or ramp; app detects the region in the background | High | Medium-high | ~$10–25 per beacon | Battery swaps, signal reflections; **works underground** | Good |
| 7 | **Car Bluetooth / CarPlay link** | Phone disconnects from the car inside the geofence → parked | High | High | $0 | Not every car has Bluetooth; needs native code | Good |
| 8 | **Plate camera (ALPR) at gate** | Camera + detector + OCR → plate → booking | Very high | Very high | ~$100–250 per lane (Pi/old phone + camera) | Lighting, angles, dirty plates; Lebanese plate format needs custom OCR checks | **Sensitive**: process on-device, store hashes |
| 9 | **Attendant phone ALPR** | Attendant points the phone at a plate → OCR → lookup | Very high | Very high | $0 | Still needs a person, but 3× faster than typing | Same as #8, but no fixed camera |
| 10 | **Per-spot sensors** (magnetometer/ultrasonic) | Sensor reports occupied or free per spot | Occupancy only | Occupancy only | ~$30–100 **per spot** | Doesn't say *which* car; expensive | Good |

### 3.2 Recommended: **hybrid evidence fusion with confidence tiers**

No single signal is reliable everywhere, so each one is treated as **evidence** that adds to or subtracts from one probability:

```
logit P(arrived) = logit(prior) + Σ  log LR(signal_i) · freshness_i

prior  ← booking exists for this lot/time window
       ← C2 pattern: P(user arrives at this lot around now)   ← the link to C2
LR     ← per-signal likelihood ratios (geofence ≈ 4, motion switch ≈ 8,
          BLE ≈ 15, plate match ≈ 50, gate QR ≈ 200, attendant = definitive)
          starting values, tuned with field/simulated data
```

| Fused P | Action | Who acts |
|---|---|---|
| ≥ 0.95 | **Auto check-in/out**, flagged `method=auto`, reversible | Nobody |
| 0.60–0.95 | Push "Parked at AUB Lot B? ✓ Yes / ✗ No" → **+points** for confirming | Driver (one tap) |
| < 0.60 at T+10 min | Task appears in the attendant's "Needs eyes" queue | Attendant |
| Contradiction (e.g. QR at lot X, GPS at lot Y) | Flag as an anomaly; never auto-confirmed | Attendant |

**Why this wins:** it is explainable (the attendant sees *why*: "geofence 07:58 + parked-motion 08:01 + Monday-8AM routine"), it works in any lot (each lot type just has a different set of available signals), and it turns the attendant from a data-entry clerk into an **exception handler**.

### 3.3 Deployment kits by lot type (feasibility answer)
| Lot type | Kit | Hardware cost | Expected auto-confirm rate* |
|---|---|---|---|
| Open-air, no staff | Geofence + motion + printed gate QR (+ NFC) | ~$0–5 | Medium |
| Open-air with attendant | Above + attendant app with phone ALPR | ~$0 | High |
| **Underground / indoor** | BLE beacon at the ramp + rotating QR + NFC (GPS useless) | ~$40–100 | Medium-high |
| Gated, high volume | Gate ALPR camera + BLE | ~$150–300 per lane | Very high |
| **Valet** | Attendant-led scan (booking QR or plate) at handover; driver phone signals ignored for departure | ~$0 | n/a (attendant-led) |

\* To be measured. We will report *measured* figures from our simulation/field test and label everything else as estimates.

### 3.4 Departure — the harder half
Arrival is easy to detect because people pass a gate. Departure goes wrong because people simply drive away. Departure evidence, strongest first: gate ALPR/BLE exit → motion walking→vehicle + leaving the fence → car Bluetooth reconnect + leaving the fence → driver confirmation → **arrival elsewhere** (the car can't be in two lots) → booking end time + all signals silent → *inferred* departure (flagged as inferred, at lower confidence, and sent for attendant spot-check).

### 3.5 Incentive design
| Incentive | Mechanic | Why it works |
|---|---|---|
| **Points per confirmation** | +10 for confirmed arrival, +10 departure, streak multiplier | Simple habit loop |
| **Grace-time bank** | Verified departures earn free grace minutes (e.g. 5 min each, capped) to absorb a future overstay | Gives back the thing drivers fear most: overstay fees |
| **Trust tier** ("GoPark Verified") | Reliable confirmers get hands-free auto check-in, priority on sold-out lots, pay-after | Status + convenience, no direct cash cost |
| **Spot-hold guarantee** | Confirmed "on my way" (geofence approach) holds the spot past the start time | Directly useful |
| **Anti-fraud** | Points only when server-side evidence agrees (≥ 2 independent signals or attendant); daily cap; anomaly flags | Prevents farming points with a spoofed GPS |

### 3.6 Attendant app — screens
1. **Lot overview**: capacity bar, free/reserved/occupied, a grid of spots if spots exist, "expected in the next 30 min" strip (fed by C2 + bookings).
2. **Scan**: one big button; camera reads QR *or* plate; manual entry as fallback. Result card: ✅ valid / ⚠ wrong lot / ⚠ early / ⚠ expired / ❌ not found → "Register walk-in".
3. **Needs eyes queue**: low-confidence arrivals, unconfirmed departures, overstays, plate mismatches. Swipe to resolve.
4. **Alerts**: upcoming arrivals, overstays (end + grace), unconfirmed departures, likely no-shows.
5. **Activity history**: every state change with actor, method (auto/driver/attendant), device, synced/pending badge.
6. **Sync indicator**: online / offline with N pending changes. Never blocks the attendant.

UX rules: thumb-reachable, high contrast for sunlight, Arabic/English (RTL ready), haptic and sound feedback on scan, ≤ 2 taps for the common path.

### 3.7 Resilience design
| Failure | Handling |
|---|---|
| **No connectivity** (attendant) | Today's bookings for the lot cached locally; **booking QR is signed (Ed25519)** so it can be checked offline with the public key; actions go to a local outbox |
| **Duplicate scan / double tap** | Every action carries a client-generated UUIDv7 → idempotent on the server |
| **Two attendants, conflicting actions offline** | Event-sourced log, ordered by `observed_at` (+ device sequence); impossible orderings (checkout before checkin) → anomaly queue |
| **Wrong lot / wrong time / wrong plate** | Explicit validation result; attendant can override, and every override is logged with a reason |
| **Unknown vehicle** | Register a walk-in (keeps occupancy correct) |
| **Missed check-in** | Signals arrive later (driver phone offline underground) → backfilled with the original timestamp |
| **Missed check-out** | Inferred departure (§3.4) + alert |
| **Phone clock skew** | Server stores both `observed_at` (device) and `received_at`; corrects using sync handshake offset |

---

## 4. Challenge 2 — solution paths for pattern recognition

### 4.1 Options explored
| # | Approach | Strengths | Weaknesses | Role in our solution |
|---|---|---|---|---|
| 1 | **Frequency rules** (count by weekday × hour × location) | Simple, interpretable | Can't separate coincidence; brittle thresholds | Baseline |
| 2 | **Bayesian recurrence** (Beta-Binomial per slot, decayed, population prior) | Handles sparse data via shrinkage; confidence comes for free; separates habit from coincidence | Needs slot definitions | **Core scorer** |
| 3 | **Clustering** (DBSCAN/HDBSCAN on circular time-of-week × location) | Finds *several* routines per user; tolerates jittery times | Parameter tuning | **Routine discovery** |
| 4 | **Change-point detection** (two-window test / Bayesian online CPD) | Catches changing routines (semester switch, new job) | Needs ≥ ~6 weeks of history | **Drift layer** |
| 5 | **Global supervised model** (LightGBM: "will user book slot s next week?") | Learns patterns shared across users; good probabilities; uses all features | Less interpretable; needs enough users | **Optional booster**, kept only if the backtest shows it beats #2 |
| 6 | Sequence models (LSTM/Transformer) | Powerful with big data | Overkill, opaque, needs data we lack | ✗ |
| 7 | LLM | Natural-language explanations, an ops "ask the data" chat | Cost, nondeterminism, external dependency | ✗ Decided: no LLM. Reasons come from templates, which are deterministic and auditable |

### 4.2 Recommended pipeline
```
bookings.csv/json ─▶ 1 Ingest & clean ─▶ 2 Routine discovery ─▶ 3 Recurrence scoring
                                                                     │
       export CSV/JSON/API ◀─ 7 Explain ◀─ 6 Reminder policy ◀─ 5 Calibrate ◀─ 4 Next-booking
```

1. **Ingest & clean**: validate the schema, convert times to `Asia/Beirut`, de-duplicate, compute weekday, start minute, duration and ISO week. Status weights: completed 1.0, cancelled 0.4 (shows intent), no-show 0.6; cancellations also feed a `cancel_rate` that lowers confidence.
2. **Routine discovery**: per user and location, cluster start times on a circular 24 h scale (eps ≈ 45 min). Each time cluster's weekday spread gives a candidate pattern (e.g. {Mon, Wed, Fri} 08:00 ± 12 min @ AUB). Labels are tidied: Mon–Fri → "Weekdays", fewer than 3 hits → no routine.
3. **Recurrence scoring (habit vs coincidence)**: for each candidate (weekday, location, time window), over the weeks the user was **active** (excluding detected inactive spans and holidays):
   `hits ~ Binomial(active_weeks, p)`, `p ~ Beta(α₀, β₀)` from a **population prior** (empirical Bayes), with **exponential decay** (half-life ≈ 4 weeks) so recent weeks count more.
   A slot is a *habit* if `P(p > 0.5) ≥ 0.8` **and** support ≥ 3 occurrences across ≥ 3 distinct weeks. Otherwise it's *occasional*.
4. **Next-booking prediction**: list the upcoming slots for the next 14 days, `score = posterior mean p × activity factor` (the activity factor decays when the gap since the last booking exceeds 2× the user's typical gap). Return the earliest slot above the threshold, or the best one if none passes. Occasional users fall back to a gap model ("likely within 7 days at Hamra, ~40%").
   *Optional:* a LightGBM global model takes (user, slot) features — recurrence posterior, last-4-same-weekday flags, EWMA frequency, recency, cancel rate, location popularity — and is blended in only if it wins the backtest.
5. **Calibration**: isotonic regression on backtest outputs, so that "80%" really means 80% of such predictions came true.
6. **Reminder policy**: remind when `confidence ≥ τ`, with τ picked where backtest **precision ≥ 0.8** (default ~0.7). Lead time: evening before for morning slots, 2 h before otherwise (adjustable; see the booking-creation-time gap in §8). Suppress if already booked, inactive, or a reminder went out in the last 48 h.
7. **Explain**: a template-generated reason, e.g. *"Booked AUB on 9 of the last 10 Mondays, usually 07:55–08:10. Last seen 3 days ago."* / *"Routine changed: Tue/Thu at LAU replaced Mon/Wed at AUB since Oct 6."*

### 4.3 Output schema (per user × pattern row)
```json
{
  "user_id": "U001",
  "pattern_id": "U001-P1",
  "pattern_type": "recurring | occasional | emerging | ended",
  "days": ["Mon","Wed","Fri"],
  "label": "Mon / Wed / Fri",
  "time_window": {"median": "08:00", "spread_min": 12},
  "location_id": "AUB",
  "support": 23, "active_weeks": 10,
  "recurrence_prob": 0.91,
  "next_booking": {"at": "2026-11-02T08:00+02:00", "location_id": "AUB"},
  "confidence": 0.92,
  "recommend_reminder": true,
  "reminder_at": "2026-11-01T20:00+02:00",
  "last_booking_date": "2026-10-30",
  "frequency_per_week": 2.8,
  "inactivity_detected": false,
  "cancel_rate": 0.04,
  "reason": "Booked AUB on 9 of the last 10 Mondays ...",
  "model_version": "rules-0.3+gbm-0.1"
}
```
Users with several routines get one row per pattern plus a single primary row for the "next likely booking" summary table.

### 4.4 Evaluation plan
- **Rolling-origin backtest**: at each of the last 4 weekly cutoffs, train on history before the cutoff and predict the next 7 days.
- **Metrics**: next-booking hit rate (same day + location, start within ±60 min); time MAE; **precision and recall of reminder decisions** at τ; **Brier score + reliability diagram** (calibration); pattern recovery vs synthetic ground truth (Jaccard of day sets, location accuracy).
- **Baselines to beat**: "same as last week", "most frequent weekday + modal time", plain frequency rules.
- Output an `evaluation_report.html` (charts) + `metrics.json`.

### 4.5 Synthetic data generator (needed unless GoPark provides data)
Persona mix with **known ground truth**: daily commuter, MWF student, Tue/Thu, multi-location (gym + office), occasional, **routine-changer** (switches mid-semester), noisy timer (±40 min), heavy canceller, new user (sparse), churned user (goes inactive). Includes Lebanese holidays and AUB term breaks. About 500 users × 16 weeks, seedable. Doubles as the C1 demo data. Each booking also gets a `created_at` (persona-specific lead time) and a plate. The engine works without `created_at` but uses it for reminder timing when present. Each persona also gets matching driver trip traces (GPS + motion + beacon) for the C1 simulator.

---

## 5. How the challenges connect

| Direction | Hook | Value |
|---|---|---|
| C2 → C1 | **Prior in evidence fusion** | Faster, more confident auto check-in for routine users |
| C2 → C1 | "Expected in the next 30 min" in the attendant app, including **likely-but-unbooked** regulars | Staffing and capacity planning |
| C2 → C1 | Per-user no-show and overstay risk | Smarter alerts (release a spot early, watch for overstay) |
| C1 → C2 | Actual arrival/departure timestamps, no-shows, overstays | Better labels; patterns on *real* behaviour, not just booked intent |
| C1 → C2 | Confirmation reliability score | Feature for reminder targeting and trust tier |
| Both | Incentives ledger | Reminders can offer "book your usual Monday spot, +20 pts" |

---

## 6. System architecture

### 6.1 Components
```
┌──────────────────────────── Clients ─────────────────────────────┐
│ Mobile app (Expo/React Native)       Ops dashboard (web)         │
│  ├─ Attendant mode (offline-first)    ├─ Lot live view           │
│  └─ Driver mode (signals, confirm,    ├─ Pattern table + export  │
│     points, QR)                       └─ Evaluation report       │
└───────┬──────────────────────────────────────────┬───────────────┘
        │ REST + WebSocket (sync, push)            │
┌───────▼──────────────────────────────────────────▼───────────────┐
│ API service (FastAPI, Python)                                    │
│  ├─ Bookings adapter  (mock now; GoPark API later)               │
│  ├─ Sync / Events     (idempotent ingest, event log, projections)│
│  ├─ Presence Fusion   (signals → P(arrived/departed) → action)   │
│  ├─ Alerts engine     (scheduler: overstay, no-show, unconfirmed)│
│  ├─ Incentives ledger (points, grace bank, tiers, fraud checks)  │
│  └─ Patterns API      (serves C2 outputs; triggers runs)         │
├──────────────────────────────────────────────────────────────────┤
│ Pattern engine (Python package, also a CLI)  ← nightly + on demand│
├──────────────────────────────────────────────────────────────────┤
│ PostgreSQL (or SQLite for the hackathon)   │  Object store: none │
└──────────────────────────────────────────────────────────────────┘
 Optional edge: Raspberry Pi / old phone gate cam (ALPR) · BLE beacon · gate QR screen
 Tools: synthetic data generator · driver trip simulator (replays GPS+motion traces)
```

### 6.2 Stack options
| Option | Mobile | Backend | ML | Pros | Cons |
|---|---|---|---|---|---|
| **S1 ✅ chosen** | Expo / React Native (TS) | FastAPI | pandas, scikit-learn, LightGBM | One backend language shared with ML; Expo is quick and supports camera/QR, location, background tasks | Two languages (TS + Python) |
| S2 | Flutter | FastAPI | same | Very polished UI, good offline packages | Fewer team members usually know Dart |
| S3 | PWA (Next.js) | Next.js API + Python worker | same | Fastest to demo on any device | **No background geofencing/BLE in a PWA**, which weakens the C1 innovation |
| S4 | Expo | Supabase (Postgres + realtime + auth) + Python engine | same | Hosted DB/realtime for free | Offline sync is harder to control |

Local storage on the attendant device: `expo-sqlite` + a home-built outbox (simple and transparent). Push: Expo Notifications. Motion: native activity-recognition module, or **simulator replay** for the demo.

### 6.3 Core data model
```
users(id, name, lang, reliability_score, tier)
vehicles(id, user_id, plate_norm, plate_display)
lots(id, name, geo_polygon, capacity, kind[open|underground|gated|valet], beacon_uuid, gate_secret)
spaces(id, lot_id, label)                                  -- optional
bookings(id, user_id, vehicle_id, lot_id, space_id?, start_at, end_at, status, created_at?)
signals(id, client_event_id, user_id?, device_id, lot_id, source, kind[enter|exit|park|unpark|scan],
        observed_at, received_at, payload_json)            -- raw evidence, append-only
stays(id, booking_id?, lot_id, plate_norm, state, in_at, in_method, in_conf,
      out_at, out_method, out_conf, inferred_flags)        -- projection
audit_log(id, client_event_id UNIQUE, actor_type, actor_id, action, entity, before, after,
          observed_at, received_at, device_id)
alerts(id, type, lot_id, stay_id?, booking_id?, severity, status, created_at, resolved_by, resolved_at)
points_ledger(id, user_id, delta, reason, ref_event_id)
user_patterns(... schema §4.3 ..., run_id)
pattern_runs(id, started_at, data_cutoff, params_json, metrics_json)
```

### 6.4 Stay state machine
```
BOOKED ─(T-30m)─▶ EXPECTED ─(weak signal)─▶ ARRIVING ─(P≥.95 | confirm | scan)─▶ CHECKED_IN
   │                  │                                                       │
   │                  └──(end passed, no evidence)──▶ NO_SHOW                 ├─(now>end+grace)─▶ OVERSTAY
   └─(cancel)─▶ CANCELLED                                                     │
                                       CHECKED_OUT ◀─(P≥.95 | confirm | scan)─┤
                                             ▲                                └─(end passed, silent)─▶ UNCONFIRMED_DEPARTURE
                                             └────────(inferred / attendant)────────────────────────────┘
```

### 6.5 Key APIs (sketch)
```
POST /sync/push            {events:[...]}  → {acks, conflicts}
GET  /sync/pull?since=…    → changed bookings/stays/alerts for device's lot
POST /signals              driver-app evidence
POST /scan                 {qr|plate|booking_id, lot_id} → validation result
POST /stays/{id}/confirm   driver one-tap
GET  /lots/{id}/overview
GET  /alerts?lot_id=…
POST /patterns/run         {cutoff?, params?}
GET  /patterns?format=csv|json   GET /patterns/{user_id}
GET  /patterns/evaluation
```

### 6.6 Security and privacy
- QR = `base64(booking_id|lot|plate_hash|start|end|nonce)` + Ed25519 signature; public key cached on attendant devices.
- Rotating gate code = `HMAC(gate_secret, floor(t/30s))`; accepted within ±1 window of the server's received time.
- Location: **geofence events only, no continuous tracking**; opt-in, with a clear "why" screen; raw signals deleted after 30 days, only stay timestamps kept.
- Plates: OCR on the device; store a normalized plate plus a salted hash for matching; images never leave the device (gate camera keeps a ≤ 24 h rolling buffer for disputes).
- Pattern engine runs on pseudonymous IDs; aligned with Lebanon's Law 81/2018 (personal data) and GDPR-style minimization.
- Role-based access: attendants see only their lot; drivers see only their own data.

---

## 7. Demo script (≈ 5 min)

1. **Dashboard, Sunday night**: the pattern engine has run. Table shows U001 "Mon/Wed/Fri 8 AM · AUB · 92%" with a reason, U00x "routine changed", U004 "occasional, no reminder". Click to export CSV/JSON.
2. **Monday 07:55, AUB lot**: the attendant app's "Expected soon" lists U001.
3. **Driver simulator** replays U001's trip: geofence enter → motion "vehicle→walking" → fused P = 0.97 → **auto check-in** appears live on the attendant app with its reason; driver gets +10 pts.
4. **Second driver, weak signals** (GPS drift) → one-tap confirmation prompt → confirmed.
5. **Third car, phone dead** → attendant scans the plate with the camera → booking found → checked in.
6. **Airplane mode on the attendant phone** → 2 scans queued offline (signed QR still validates) → reconnect → synced, history shows "synced 08:12".
7. **Wrong-lot QR** → clear warning; **unknown plate** → walk-in.
8. **Fast-forward**: overstay alert; unconfirmed departure → inferred check-out with flag.
9. **Evaluation page**: backtest hit rate vs baselines, reliability curve, reminder precision at τ.
10. **Closing slide**: cost table per lot kit, measured accuracy, limitations, next steps.

---

## 8. Gaps the brief leaves open, and our answers

| Gap | Why it matters | Proposed solution |
|---|---|---|
| **Phone ≠ car** (passengers, valet, phone dies) | Geofence alone gives false arrivals and departures | Motion-state + car-Bluetooth signals; valet lots use an attendant-led kit; ignore non-driver phones (only the booking owner's device counts) |
| **Underground lots: no GPS or signal** | Geofence and sync fail exactly where many Beirut garages are | BLE beacon + QR/NFC; driver app stores evidence offline with timestamps and backfills |
| **No booking-creation timestamp in C2 input** | Reminder timing needs to know *when* people usually book | Default heuristics (evening before / T-2h); ask GoPark for `created_at` (Q3); if present, reminder = median booking lead time + buffer |
| **No real data** | Model validity is unprovable | Persona generator with ground truth + held-out backtest; plug in GoPark data the moment it's available |
| **Walk-ins / non-GoPark cars** | Occupancy wrong if only bookings are tracked | Walk-in registration; optional spot sensors for occupancy truth |
| **Spot-level vs lot-level** | Overview design depends on it | Support both; spot grid only shows if `spaces` exist (Q4) |
| **Multiple vehicles per user, plate changes** | Plate lookup mismatches | Plate→vehicle→user mapping; mismatch = warning, not rejection |
| **Incentive fraud (GPS spoofing)** | Points farming | Points need ≥ 2 independent signals; caps; anomaly score |
| **Clock / DST issues** | Lebanon's 2023 DST split shows it can happen | UTC everywhere + IANA tz; device clock offset correction |
| **Holidays & term breaks** | False "inactivity" and false "routine ended" | Holiday calendar excludes those weeks from the active-week count |
| **Cold start** (new users, new lots) | No pattern yet | Population priors per location; "insufficient history" label |
| **Attendant adoption** | Fancy tech fails if staff ignore it | ≤ 2 taps, Arabic UI, works offline, sound/haptic feedback |
| **Battery / permission refusal** | Many drivers deny "always" location | The system degrades gracefully to QR/NFC + attendant; incentives for opting in |
| **Integration with GoPark backend** | Unknown API | Bookings adapter interface; mock now, swap later |

---

## 9. Build plan and cut lines

### 9.1 Team split (3–4 people)
| Person | Owns |
|---|---|
| A — Mobile | Attendant mode (scan, overview, queue, history, offline outbox) |
| B — Mobile/IoT | Driver mode (signals, confirm, points, QR) + trip simulator + BLE beacon + NFC (+ Pi gate cam if time) |
| C — Backend | FastAPI, sync, fusion, alerts, incentives, DB, dashboard live view |
| D — ML | Generator, pattern engine, evaluation, export, dashboard pattern pages |

**With 3 people:** B's work is split. Driver mode goes to A, and the simulator + BLE go to C. The Pi gate camera is dropped.

### 9.2 Phases (36–48 h event)
| Phase | Goal | Done when |
|---|---|---|
| P0 (2 h) | Repo, schema, seed data, API contract, generator v0 | Everyone builds against the same mock data |
| P1 (MVP C1) | Scan QR/booking ID → check-in/out; overview; history | Attendant can check in/out end-to-end |
| P1 (MVP C2) | Ingest → rules → pattern table → CSV/JSON | Table matches persona ground truth on obvious users |
| P2 | Offline outbox + signed QR; alerts; Bayesian scoring + backtest | Airplane-mode demo works; metrics.json produced |
| P3 | Fusion + driver simulator + one-tap confirm + points; calibration + reminders + reasons | Auto check-in demo works |
| P4 (link) | C2 priors → fusion; "expected soon"; actuals → C2 | Loop is visible in the demo |
| P5 (stretch) | Real BLE beacon + NFC tap in driver app, attendant-phone plate OCR, Pi gate camera, LightGBM booster | Only if P1–P4 are solid |
| P5b (field test) | Short real test at a lot with Android + iPhone (geofence + motion + beacon); record video and measured hit rates | Video + numbers for the accuracy slide |
| P6 | Polish, demo rehearsal, slides (cost/accuracy/limits/next steps) | Two clean dry runs |

**Cut order if behind:** Pi gate cam → LightGBM → plate OCR → real BLE/NFC (simulate instead) → field-test video → driver confirm UI (keep auto + attendant). **Never cut:** offline scan, pattern table export, backtest metrics.

### 9.3 Repo layout
```
gopark-sense/
  apps/mobile/            Expo app (attendant + driver modes)
  apps/dashboard/         web dashboard
  services/api/           FastAPI (sync, fusion, alerts, incentives, patterns API)
  services/patterns/      Python package + CLI: ingest, mine, score, calibrate, explain, evaluate
  tools/generator/        synthetic personas → bookings + trips
  tools/simulator/        replays driver GPS/motion/BLE traces into /signals
  edge/gatecam/           (stretch) Pi/phone ALPR
  data/  docs/  slides/
```

---

## 10. Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| Background location/motion APIs are hard to get working in Expo within hackathon time | High | Simulator replays traces into the same `/signals` endpoint; real sensing is a stretch goal |
| Lebanese plate OCR accuracy | Medium | Attendant confirms OCR output; regex check (letter + up to 6 digits, to be checked on real plates); manual fallback |
| Synthetic data looks "too easy" | Medium | Add noise personas, report results on the hard personas separately |
| Scope creep from building both challenges | High | Cut lines in §9.2; each challenge's MVP stands alone |
| Live demo network failure | Medium | Everything runs on a laptop LAN/hotspot; recorded backup video |

---

## 11. Decisions (answered 2026-10-31)

| # | Question | Decision | Effect on the plan |
|---|---|---|---|
| 1 | Combined or primary challenge | **Combined allowed** | Path D, both halves weighted equally |
| 2 | Duration / team | **36–48 h, 3–4 people**; team covers RN/JS, Flutter, Python/ML, Node | Phases §9.2 as drafted; 3-person fallback in §9.1 |
| 3 | Data | **Synthetic only, we generate it** | Generator is P0 (first hours) and drives both C1 and C2 demo data; add a `created_at` field so reminder lead time can be learned, and say on the slide that GoPark's real data should include it |
| 4a | Spot vs lot | **Support both** | `spaces` table optional; overview shows a spot grid only for lots that have spaces |
| 4b | Lot types | Not sure, so **our pick:** demo **open-air** (geofence + motion + gate QR) and **underground** (BLE + NFC + offline) live. Gated and valet appear as kits on the deployment slide | Generator seeds AUB (open-air), BDD (underground), Hamra (valet, attendant-led), LAU (gated, spot-level) |
| 5 | GoPark API | Not available, so **mock everything** behind the bookings adapter | No change |
| 6 | Hardware | **Android + iPhones, Raspberry Pi/webcam, BLE beacons, NFC tags** | Real BLE/NFC promoted to P5 with a field test; Pi gate cam is the first thing cut |
| 7 | Platforms | Both Android and iOS available | Expo cross-platform; demo the attendant app on Android and the driver app on iPhone to show both |
| 8 | Demo mode | **Simulated live demo + recorded field-test video** | P5b added; accuracy slide uses measured field numbers |
| 9 | Stack | **Expo + FastAPI** | S1 |
| 10 | LLM | **None** | Template-based reasons only |
| 11 | Language | **English, RTL-ready** | Use logical `start/end` styles and string tables; Arabic listed as a next step |
| 12 | Incentives | **Points + perks + small discounts** | Full §3.5 design incl. grace-time bank; add "every 10 confirmed stays = 1 h free at your usual lot" |
| 13 | Overtime payment | Not asked, so **default: out of scope.** Overstay fee is calculated and shown, but no payment flow | Overstay alert shows the estimated fee |
| 14 | Presentation | Not asked, so **default:** 5-min live demo + 3–4 slides (architecture, accuracy/cost, incentives, next steps) + backup video | §7 script fits 5 min |

### Still worth confirming with the organisers
- Exact presentation length and whether a live demo is required.
- Whether GoPark can share even a small real sample (or just field names and typical volumes) to sanity-check the generator.
