# How GoPark Sense detects arrivals and departures (Challenge 1)

> **Final deployment (2026-11-01):**
> - **Gated lots:** a gate camera with the ML model (plate + car look).
> - **Single spots:** a presence sensor per spot, no camera.
> - **Ungated lots:** no detection hardware; the valet/attendant checks cars in and out in the app.
>
> The technique chain below describes every signal the system supports.

**Scope (owner decision, 2026-11-01):** lots with a **plate camera and presence sensors at the entry and exit**.
Out of scope for now:
- lots without an entry/exit camera;
- NFC stickers.

No single method is good enough on its own. Each technique below has a weakness, and the **next one in the
list covers it**. Fusion (`api/ops/fusion.py`) adds them all up into one confidence score per booking:

| Confidence | What happens |
|---|---|
| ≥ 0.95 | Checked in or out automatically |
| 0.60–0.95 | Waits for the driver's answer to the confirmation message |
| < 0.60 | Flag for the attendant ("Needs eyes") |

Status:
- **built**: on `main`.
- **planned**: see `docs/briefs/C1-detection.md`.

## The chain

| # | Technique | What it gives | Why it is not enough alone | Covered by |
|---|---|---|---|---|
| 1 | **Booking + Rhythm prediction** (built) | Who is expected at which lot and when (e.g. "Rania, Mon/Wed ~08:10, 0.83"). Sets the starting confidence and the "expected soon" list. | It is a prediction, not an observation. People cancel, come late or don't come. | 2 |
| 2 | **Phone GPS + "stopped driving"** (built) | An early heads-up ("3 min away") that puts the driver on the expected list, plus arrival evidence. | City GPS is off by 20–50 m, so "near" isn't "inside". No GPS underground. Phones delay background apps. The phone may be dead or in another car. Exit alerts arrive late. | 3 |
| 3 | **Gate presence sensors**: 2 per lane, direction from trigger order (built) | Every car in or out, at the exact second, including walk-ins and dead phones. This makes **departures and billing exact**. | It knows *a* car passed, not *which* car. | 4 |
| 4 | **Plate camera** at entry and exit, paired with the sensor within ±8 s (built; never field-tested) | *Which* car, automatically, on entry and exit. | Fails on dirty, bent, missing or temporary plates, at night, with glare, at bad angles, and with cars bumper to bumper. | 5 |
| 5 | **Car-look match**: CLIP zero-shot colour, body type and make on the car crop, against the cars expected right now (planned, T1) | A suggestion of who it is when no plate was read ("probably Rania's blue BMW SUV"). | Only works when the expected cars look different. Three white Kias can't be told apart. **It never checks anyone in on its own.** | 6 |
| 6 | **Parking confirmation message** to the driver (planned, T3; the driver-confirm API is built) | After every check-in: "Your car B123456 was checked in at BDD at 08:12. Is this you? **Yes / Not me**." After every check-out: the time and the amount. It settles the uncertain cases and catches mistakes. | Drivers may not answer, or may tap "Yes" without reading. | 7 |
| 7 | **Attendant**: scan booking QR / booking ID / plate, walk-ins, "Needs eyes" list (built; works offline) | The last resort that always works. It also handles "Not me" disputes and walk-ins. | Costs staff time. That's why it comes last. | — |

## The confirmation message (what the driver sees)

| When | Message | Answers |
|---|---|---|
| Auto check-in (≥ 0.95) | "Checked in at BDD, 08:12, car B123456. Not you? Tell us." | *Not me* (optional) |
| Uncertain check-in (0.60–0.95, e.g. the car-look match or a weak plate read) | "Did you just park at BDD? (blue BMW, 08:12)" | **Yes** / **Not me** |
| Check-out | "You left BDD at 10:42. 2 h 30 min, $X." | *That's wrong* (optional) |
| Overstay | "Your booking ended 15 min ago. $Y extra so far." | — |

What each answer does:
- **No answer:** an auto check-in stays as it is. An uncertain one stays open for the attendant after 10 minutes.
- **Yes:** the "Yes" counts as strong evidence only if the phone is near the lot when it answers (GPS). Answering from home does not check a car in.
- **Not me:** sends a dispute alert to the attendant.
- **Points:** a confirmation earns points only when the sensors already agree (`config/rewards.yaml`).

## Cheating the exit fee

| What they do | What the system sees | Result |
|---|---|---|
| Leave the lot and park on the street | Exit sensors + plate | Checked out correctly; they really left |
| Leave and come back in | Exit, then a new entry + plate | A new stay, billed |
| Move to another spot inside the lot | No gate crossing | The stay keeps running (billing is per stay, not per spot) |

## Where a message is not enough (why NFC might come back later)

1. **Someone else's car under your booking.** The plate isn't read, the car-look match is ambiguous, and the driver answers "Yes" while not actually there (to keep their spot). **Covered** by counting a "Yes" only when the phone's GPS is at the lot.
2. **Underground with no signal.** The message arrives late. **Covered** by the app's offline outbox: the answer is sent when the signal comes back, and the sensor and camera already did the check-in.
3. **Plate unreadable + similar cars + dead phone.** Nobody can be asked. NFC wouldn't help either (the phone is dead). **The attendant handles it.**
4. **Lots without an entry/exit camera.** This is the only real case for NFC (proof the driver is physically there). Out of scope for now.
