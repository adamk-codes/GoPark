# Synthetic data generator (owner: P4)

Writes the files in CONTRACT.md §3, seeded and reproducible. Written without reading `rhythm/`.

```bash
uv run python -m tools.generator --users 300 --weeks 26 --seed 42
```

| Flag | Default | What it does |
|---|---|---|
| `--users`, `--weeks`, `--seed` | 300, 26, 42 | Size and seed. Same flags → byte-identical output. |
| `--history-end` | 2026-11-15 | Last history day. Later bookings are the live week, status `booked`. |
| `--future-days` | 7 | Length of the live week. |
| `--mix` | built-in shares | Persona weights, e.g. `--mix fader=2,lapsed,occasional=0.5` (unlisted personas get 0). |
| `--messy` | off | Injects dirty rows into history (see below) and writes `messy_log.json`. |
| `--include-hidden` | off | Adds the two hidden personas from `hidden_personas.py` (gitignored, P4 only). |
| `--no-traces` | off | Skip `traces/`. |
| `--no-cameras` | off | Traces without `camera_plate` / `camera_spot` (the beacon/NFC kit). |

Output in `--out` (default `data/`): `bookings.csv`, `stays.csv`, `truth.json`, `traces/`, and `messy_log.json` with `--messy`.
`truth.json → generated_with` records the mix, persona counts, capacity shifts/drops, messy counts and trace scenario counts.

## Personas

| Persona | Share | Behaviour | Truth |
|---|---|---|---|
| `weekday_commuter` | 14% | Mon–Fri ~9:00, office/open lot, 8–9.5 h | 1 routine |
| `mwf_student` | 12% | Mon/Wed/Fri ~8:00 at AUB/LAU, skips term breaks | 1 routine |
| `tue_thu` | 5% | Tue/Thu ~10:00 at LAU | 1 routine |
| `occasional` | 13% | Random days and lots, 0.15–0.6 per week | no routine |
| `routine_switcher` | 8% | Changes days, lot or both on a switch date 40–75% into the history (≥ 4 weeks before the end) | 2 routines; first `active_to` = day before second `active_from`; `switch: {date, changed}` |
| `fader` | 7% | Attendance halves every 2.5–5 weeks after `fade_from` | `active_to` = when attendance drops below 0.25 (`null` if still above at history end); `attendance: {p_start, fade_from, half_life_weeks}` |
| `noisy_timer` | 7% | Real routine, start times uniform ±40–60 min | `jitter_min` 40–60 |
| `heavy_canceller` | 7% | Real routine, 30–50% cancelled; 20–50% of cancels rebooked 15–90 min later the same day, closer to the time | 1 routine; `cancel_rate` |
| `multi_routine` | 7% | Weekdays at BDD/ACHRAFIEH/VERDUN + Saturday ~11:30 in HAMRA | 2 routines |
| `coincidence_trap` | 7% | Random booker who lands on the same weekday, ~time and lot 3–4 times in non-consecutive weeks | no routine; `coincidence: {day, location_id, count}` |
| `new_user` | 6% | Joins 1–3 weeks before history end, already on a routine | `active_from` = join date |
| `lapsed` | 7% | Strong routine, stops for good 40–75% into the history | `active_to` = last day |
| hidden ×2 | 4% each | Only with `--include-hidden` | |

Every routine also has `jitter_min` (the start-time spread used). Holidays: 90% skipped. Statuses: per-user cancel 2–15%, no-show 2%.

**Capacity.** Bookings are taken first come, first served by `created_at`. If the lot is full in the 15-minute slot a booking starts in, it moves ±15 then ±30 min, else it is dropped (sold out). Cancelled bookings don't hold a space. At 300 users nothing is full. The lots are small (410 spaces in total), so at 2,000 users about 2% shift and 42% drop, mostly all-day commuters in the underground garages.

## `--messy`

Only history rows are touched; the live `booked` week stays clean so the ops demo loads. Each damaged row gets one kind; `messy_log.json` lists the count and booking ids per kind.

| Kind | Rate | Example |
|---|---|---|
| `status_noise` | 3% | `Completed`, ` cancelled `, `CANCELLED`, `canceled`, `NO_SHOW` |
| `missing_end_time` | 0.5% | `end_time` empty |
| `end_before_start` | 0.3% | `start_time` and `end_time` swapped |
| `no_offset` | 0.5% | `2026-06-01T08:00:00` (no `+03:00`) in start and end |
| `duplicate` | 1% | Exact copy of the row right after it (same `booking_id`) |

## Traces (`traces/`)

One `traces/<booking_id>.jsonl` per live-week booking. Each line is a `contract/schemas/signal.schema.json` signal plus `truth`: does this signal reflect the booked car really arriving at / leaving that lot? Lines are sorted by `observed_at`.
- **Driver signals** have `device_id = dev-<user_id>`, `user_id` and `booking_id`.
- **Camera signals** have `device_id = cam-<lot>-gate|ramp|overhead`, `user_id` and `booking_id` null.
- **Offline phones.** Signals buffered underground keep their original `observed_at` and add `payload.sent_at`, when the phone reconnected. **A replayer sends each line at `payload.sent_at` if present, else at `observed_at`.**

`traces/index.csv` has one row per trace: `booking_id, user_id, lot_id, lot_kind, scenario, variant, arrives, actual_arrival, actual_departure, signals`. Use it to score detection (C1) without reading the lines.

### Scenario mix (chosen per booking by lot kind)

| Scenario | open (AUB, VERDUN) | gated (LAU) | underground (BDD, ACHRAFIEH) | valet (HAMRA) | Signals |
|---|---|---|---|---|---|
| `clean` | 55% | 50% | — | 40% | geofence enter (5–30 m) → motion park → motion unpark → geofence exit |
| `weak_gps` | 20% | 20% | — | 25% | geofence enter with 120–450 m accuracy; variant `late_fence` arrives 2–12 min late; 60% get a coarse exit; no motion |
| `underground` | — | — | 75% | — | 50% street-level geofence just before the ramp; variant `ble` (80%): BLE enter/exit at the ramp + motion, all buffered (`sent_at` 4–20 min after arrival, 0.5–3 min after departure); `no_ble`: motion only |
| `phone_dead` | 10% | 15% | 12% | 25% | no driver signals; cameras only (nothing at HAMRA, so the attendant must scan) |
| `contradiction` | 10% | 10% | 8% | 5% | geofence enter at the **nearest other lot** (`truth: false`) + motion park/unpark at the booked lot |
| `no_show` | 5% | 5% | 5% | 5% | car never comes; 50% (not underground) are a `drive_by`: geofence enter + exit 1–4 min apart, `truth: false` |

**Cameras** (camera-first; the kit is in `CAMERA_KIT` in `traces.py`). Every non-valet lot has an entrance `camera_plate` (enter/exit, `payload: {plate_norm, ocr_conf, camera, direction}`). LAU also has an overhead `camera_spot` (`occupied` / `vacant`, `payload.space_id` such as `LAU-07`). 10% of camera events are missed. 5% of plates are misread by one digit, with `ocr_conf` 0.45–0.75. `--no-cameras` turns them all off.

**Times.** Arrival = start + N(−5, 8) min. Departure = end + N(−5, 15) min, or an overstay of +20–60 min (8%).

**Counts** with the defaults (`--users 300 --weeks 26 --seed 42`): 674 live bookings → underground 207, clean 200, weak_gps 83, phone_dead 82, contradiction 66, no_show 36.

## Hidden personas
`tools/generator/hidden_personas.py` is in `.gitignore` until the final evaluation. It defines `HIDDEN_PERSONAS`, in the same shape as `PERSONAS`. Without the file, `--include-hidden` exits with an error.
