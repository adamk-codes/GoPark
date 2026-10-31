# Brief P1 — Mobile app (attendant + driver)

**Branch:** `feat/mobile` · **You own:** `apps/mobile/` · **Read:** `CONTRACT.md` §1–§7, `ARCHITECTURE_PLAN.md` §3.5–§3.7, `apps/mobile/AGENTS.md`

## Mission
The attendant must be able to do everything in ≤ 2 taps, even with no internet. The driver must be able to confirm a stay with one tap. This app is what judges hold in their hands during the demo.

## Where it stands (P0)
- `App.tsx`: a single screen with Attendant/Driver toggle. The attendant picks a lot, sees counts and "expected soon", and checks in/out by booking ID or plate via `POST /scan`.
- `src/api.ts`: `fetch` wrapper; `EXPO_PUBLIC_API_URL` sets the server (laptop LAN IP, port 8000).
- `src/contract.ts`: copy of `contract/types.ts` (refresh with `npm run contract`).
- Expo **SDK 58**, React Native 0.88. **Don't trust memory for Expo APIs**: follow `apps/mobile/AGENTS.md`, fetch the versioned docs, and add packages with `npx expo install <pkg>`.

## Run
```bash
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```
```bash
npm --prefix apps/mobile run start
```
Put `EXPO_PUBLIC_API_URL=http://<laptop-LAN-IP>:8000` in `apps/mobile/.env` (gitignored). Phone and laptop must be on the same Wi-Fi or hotspot.

## Phase P1 (hours 3–12): attendant core
- [ ] Move to **Expo Router** (`src/app/`): tabs `Overview`, `Scan`, `Alerts`, `History`, plus a `Driver` stack and a `Settings` screen.
- [ ] **Settings:** API URL, current lot, device ID (generate once; persist with `expo-secure-store` or AsyncStorage).
- [ ] **Scan:** camera barcode scanner (`expo-camera`) reads booking QR strings (`GP1.…`, CONTRACT §5) and sends them as `qr`; manual entry for booking ID or plate; a big result card with colour per `result` (`checked_in`/`checked_out` green; `wrong_lot`/`too_early`/`expired`/`already_in` amber; `not_found`/`invalid_qr` red); haptic + sound feedback; "Register walk-in" button when `suggest = register_walk_in` (endpoint coming from P2; mock until then).
- [ ] **Overview:** counts, spot grid when `spaces` is not null, "expected soon" list (tap prefills Scan), pull to refresh + poll every 10 s.
- [ ] **History:** `GET /audit?lot_id=` with method badges (auto / driver / attendant / inferred) and a synced/pending badge.

**Done when:** on a real phone in Expo Go you can scan a QR and plate-check a car in and out at AUB, and the dashboard shows the change.

## Phase P2 (hours 12–24): offline-first + alerts
- [ ] **Local cache** (`expo-sqlite`): today's bookings and stays for the lot from `GET /sync/pull?lot_id=`. Refresh on reconnect and every few minutes.
- [ ] **Outbox:** every attendant action becomes a `sync_event` (CONTRACT §7) with a UUIDv7 `client_event_id`. Apply it optimistically to the local cache, send via `POST /sync/push`, retry with backoff, and drop on ack. Show conflicts in the Alerts tab.
- [ ] **Sync indicator** in the header: "Online" / "Offline · 3 pending". The UI never waits on the network.
- [ ] **Offline QR check:** cache the public key from `GET /keys/qr` (P2 ships it). Verify the Ed25519 signature in JS (a pure-JS library such as `@noble/ed25519` or `tweetnacl`, chosen after checking it runs under Hermes). Then check lot, time window and a local "already used" set, exactly as in CONTRACT §5.
- [ ] **Alerts tab:** `GET /alerts?lot_id=`; resolve button (endpoint from P2); a "Needs eyes" section for `low_confidence` and `anomaly` alerts.

**Done when (demo step 6):** in airplane mode you scan 2 booking QRs → both show valid → turn the network on → they sync, and History shows them as synced with the original times.

## Phase P3 (hours 24–32): driver mode
- [ ] **My booking:** shows the signed booking QR (from P2's endpoint, rendered with a QR component) and the lot, time and plate.
- [ ] **On my way** button → `POST /signals` (`source: "driver_confirm"`, `kind: "enter"`) so the spot is held.
- [ ] **Confirm prompt:** when P2 marks a stay as `ARRIVING`, show "Parked at AUB? Yes / No" → `POST /stays/{id}/confirm`. Use polling first; push notifications only if time allows.
- [ ] **Rewards:** points, grace-minute bank, tier from `GET /users/{id}/rewards`; a "Book your usual Monday spot +20 pts" card when the user's pattern tier is `at_risk` (from `/rhythm/users/{id}`).
- [ ] **Sensing (with the hardware person, needs a development build):**
  - `expo-location` geofence enter/exit as a background task → `POST /signals` (`geofence`).
  - Hybrid kit only: BLE region (`react-native-ble-plx`) and NFC tap (`react-native-nfc-manager`) → `ble` / `nfc` signals.
  - Queue signals offline and send them later with the original `observed_at`.
  - Camera-first decision: skip BLE and keep geofence + NFC.

## Phase P4 (hours 32–40): polish
- [ ] Large touch targets, high contrast, layout using `start`/`end` (RTL-ready), all strings in one file.
- [ ] Demo device setup: lot preset to AUB, device ID `dev-attendant-1`, driver phone logged in as the demo user.
- [ ] Rehearse demo steps 2–7 (`ARCHITECTURE_PLAN.md` §12) twice on the real phones.

## Interfaces you depend on
| Need | From | Until then |
|---|---|---|
| `GET /keys/qr`, booking QR endpoint, walk-in endpoint, alert resolve | P2, phase P1 | Mock in `src/api.ts` behind a flag |
| `POST /stays/{id}/confirm`, rewards | P2, phase P3 | Mock |
| `/rhythm/users/{id}` reminder tier | P3 (exists now with baseline data) | — |

## Gotchas
- Expo Go only has its bundled native modules. BLE, NFC and background geofencing need a **development build** (`npx expo run:android` or EAS). The attendant features must keep working in plain Expo Go.
- Use the API's `now` (from `/health` or overview) for "expected soon" timing, not the phone clock: the demo clock is in November 2026.
- Android emulator reaches the laptop at `10.0.2.2`; real phones need the LAN IP.
