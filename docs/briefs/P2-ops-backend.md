# Brief P2 — Ops backend, detection fusion, simulator (integration owner)

**Branch:** `feat/ops` · **You own:** `api/ops/`, `api/db.py`, `api/schema.sql`, `api/main.py`, `api/deps.py`, `config/fusion.yaml`, `tools/simulator/`, `tools/demo.py`
**Read:** `CONTRACT.md` (all; you own §4–§7), `ARCHITECTURE_PLAN.md` §3 and §7

## Mission
Turn signals into confident check-ins and check-outs, keep the attendant app correct when it's offline, and make sure the whole demo runs from one command. You are also the **integration owner**: you merge PRs to `main` and run the smoke test after each merge.

## Where it stands (P0)
- `api/ops/routes.py`:
  - health, lots, overview (with "expected soon" from bookings + C2 priors);
  - `/scan` by booking ID or plate (check-in/out toggle, wrong lot, too early, expired, walk-in suggestion, 5-minute double-scan guard, idempotent via `processed_events`);
  - checkout, stays, audit, signal storage;
  - basic `/sync/push` replay; `/sync/pull` (today's bookings + stays);
  - alerts computed on the fly (overstay, upcoming);
  - demo clock (`/dev/clock`) and `/dev/reset`.
- Stubs: `/keys/qr`, `/stays/{id}/confirm`, rewards (points sum only).
- `api/db.py`: SQLite, UTC text times, demo clock in the `meta` table, seeding from `data/bookings.csv`. At 07:50 it pre-parks the cars that should already be in.
- Tests: `tests/test_contract.py`. Add yours as `tests/test_ops_*.py`.

## Phase P1 (hours 3–12): contract additions + correctness
First PR, **contract only** (others are waiting for it). Add to `CONTRACT.md`, `contract/schemas/` and `contract/types.ts`:
- `GET /bookings/{id}/qr` → `{qr}` (the signed booking QR string, for the driver app)
- `POST /walk_ins` `{lot_id, plate, device_id, client_event_id}` → `scan_result` with a stay that has `booking_id: null`
- `POST /alerts/{id}/resolve` `{device_id, note?}`
- `GET /stays/{id}/evidence` → the fused-confidence timeline (signals used, their contribution, P over time); the dashboard's detection log needs it
- `GET /dev/gate/{lot_id}` → `{code, valid_until}` for the rotating gate-QR screen (demo only)

Then build:
- [ ] **QR signing:** Ed25519 keypair created on first start in `keys/` (gitignored; `cryptography` is already a dependency). `/keys/qr` serves the public key, base64url. `/scan` with `qr` verifies the signature and payload and applies the same checks as booking-ID scans. Return `invalid_qr` on a bad signature.
- [ ] **Rotating gate code** `GPG1.<lot>.<code>` (CONTRACT §5) accepted from the driver app as a `gate_qr` signal.
- [ ] **Walk-ins** (keeps occupancy right).
- [ ] **Persisted alerts:**
  - a background task every 30 s, using the demo clock, creates and updates rows in `alerts`:
    - `overstay` (end + 10 min grace, with estimated fee);
    - `unconfirmed_departure` (booking ended, still CHECKED_IN, no exit evidence);
    - `no_show` (end passed, never arrived; sets the booking status);
    - `upcoming_arrival`;
  - no duplicates; a resolve endpoint.
- [ ] **Sync:**
  - a real cursor (`updated_at` on stays/bookings/alerts);
  - `sync/push` handles `walk_in`, `resolve_alert` and `override`;
  - impossible orderings (checkout before check-in, scan on an already checked-out booking) become `anomaly` alerts plus a conflict reason, never a 500.

## Phase P2 (hours 12–24): evidence fusion + simulator
- [ ] **`api/ops/fusion.py`**, called after every `/signals` batch for the affected bookings:
  ```
  logit P = logit(prior) + Σ log_lr(source) · 0.5^(age_min / freshness_half_life)
  ```
  - `prior` = the C2 arrival prior for (user, lot, today, window containing now) from `api/rhythm_api/store.latest_priors()`, else `default_prior`.
  - Weights from `config/fusion.yaml`. "Definitive" sources settle it outright.
  - Camera signals: `camera_plate` matches the booking by `plate_norm`; `camera_spot` only adds occupancy evidence.
- [ ] **Tiers:**
  - P ≥ 0.95 → stay `CHECKED_IN`, `in_method = auto`, `in_conf = P`, plus an audit entry.
  - 0.60–0.95 → stay `ARRIVING` (waiting for driver confirm).
  - Still < 0.60 after 10 min → `low_confidence` alert.
  - Contradiction (strong evidence at two lots) → `anomaly`, never auto-confirmed.
- [ ] **Departure:** the same machinery with exit signals. Also: arrival at another lot closes the old stay; end passed + silent → `UNCONFIRMED_DEPARTURE`, then an inferred checkout (`inferred = 1`) after 30 min.
- [ ] Store each fusion step so `/stays/{id}/evidence` can return the timeline.
- [ ] **Simulator `tools/simulator/`:** replays `data/traces/*.jsonl` (P4 produces them) into `/signals`, timed against the demo clock (speed factor flag). Until traces exist, generate simple scenarios inline:
  - clean arrival;
  - weak GPS only;
  - underground (no GPS; BLE or camera; phone offline then backfills);
  - phone dead (attendant scan);
  - contradiction.
- [ ] **`WS /live?lot_id=`** pushes `{type: "stay"|"alert", data}`. The dashboard and app may keep polling as a fallback.

**Done when (demo step 3):** the simulator plays U0048-style arrivals, the stay flips to auto CHECKED_IN at ≥ 0.95 with a readable evidence trail, and a weak-signal trip ends in `ARRIVING`.

## Phase P3 (hours 24–32): driver confirm, incentives, demo script
- [ ] `POST /stays/{id}/confirm` `{answer}`: yes → CHECKED_IN (`method = driver`); no → back to EXPECTED + `low_confidence` alert.
- [ ] **Incentives ledger** (`points_ledger`, ARCHITECTURE_PLAN §3.4):
  - +10 per confirmed arrival or departure, with a streak multiplier;
  - a grace-minute bank (5 min per verified departure, cap 30) applied before raising an overstay;
  - "Verified" tier after N reliable confirmations;
  - points only when ≥ 2 independent signals or an attendant agree; daily cap.
  - `GET /users/{id}/rewards` returns the real numbers.
- [ ] **`tools/demo.py`:** one command that resets the DB, sets the clock, and plays the demo scenarios in order with pauses (keypress to continue). This is the presenter's script.

## Phase P4 (hours 32–40): integration
- [ ] After every merge: `uv run pytest`, the dashboard build, a mobile typecheck, then `tools/demo.py` end to end.
- [ ] Freeze the API: only bug fixes after hour 34.

## Interfaces
| You provide | To |
|---|---|
| Contract additions above (P1, first PR) | P1, P4 |
| Fusion + evidence endpoint (P2) | P4 dashboard detection log |
| Confirm + rewards (P3) | P1 driver mode |

| You consume | From |
|---|---|
| `arrival_priors.json` (shape fixed) | P3 (baseline data exists now) |
| `data/traces/*.jsonl` | P4 (P1 phase) |
| Camera signals | Hardware/edge (posts to `/signals`) |

## Gotchas
- All DB times are UTC text; compare as text only when both are produced by `db.utc()`.
- Seeding happens on startup only when the DB is empty. Use `/dev/reset` after regenerating data.
- `config/fusion.yaml` weights are starting guesses. Note any tuning you do, so the accuracy slide can explain it.
