# How GoPark Sense detects arrivals and departures (Challenge 1)

No single method is good enough on its own. Each technique below has a weakness, and the **next one in the
list covers it**. Fusion (`api/ops/fusion.py`) adds them all up into one confidence score per booking:

| Confidence | What happens |
|---|---|
| ≥ 0.95 | Checked in or out automatically |
| 0.60–0.95 | Ask the driver (one tap) |
| < 0.60 | Flag for the attendant ("Needs eyes") |

Status:
- **built**: on `main`.
- **planned**: see `docs/briefs/C1-detection.md`.

## The chain

| # | Technique | What it gives | Why it is not enough alone | Covered by |
|---|---|---|---|---|
| 1 | **Booking + Rhythm prediction** (built) | Who is expected at which lot and when (e.g. "Rania, Mon/Wed ~08:10, 0.83"). Sets the starting confidence and the "expected soon" list. | It is a prediction, not an observation. People cancel, come late or don't come. | 2 |
| 2 | **Phone signals: geofence + "stopped driving"** (built) | An early heads-up ("3 min away") and arrival evidence, with nothing to install at the lot. | City GPS is off by 20–50 m, so "near" isn't "inside". There's no GPS underground. Phones delay or kill background apps, and many users refuse "Always" location. The phone may be dead, at home, or in another car. Exit alerts arrive late, and billing depends on the exit. | 3 |
| 3 | **Gate presence sensors**: 2 per lane, direction from trigger order (built in the API; hardware by the Cursor engineer) | Every car in or out, at the exact second, including walk-ins and dead phones. This is what makes **departures** trustworthy. | It knows *a* car passed, not *which* car. Needs a lane or gate. | 4 |
| 4 | **Plate camera** at entry/exit: old phone, plate OCR, paired with the sensor within ±8 s (built; never field-tested) | *Which* car, automatically, on entry and exit. With the sensor, a confident check-in and check-out with no human. | Fails on dirty, bent, missing or temporary plates, at night, with glare, at bad angles, and with cars bumper to bumper. Needs power, Wi-Fi and the lot owner's OK. | 5 |
| 5 | **Car-look match**: colour, body type and make by CLIP zero-shot on the car crop, against the cars expected right now (planned) | A *suggestion* of who it is when no plate was read ("probably Rania's blue BMW SUV"). | Only works when the expected cars look different. Three white Kias can't be told apart. Make/model is unreliable at night or at an angle. **It never checks anyone in on its own.** | 6, 7 |
| 6 | **Secure NFC sticker**: NTAG 424 DNA, each tap is a new signed code (planned; `nfc` signal source exists) | Strong identity (the driver's phone + this lot + now) at lots with **no camera**, or when 4 and 5 failed. Valet: "tap to call your car" *is* the check-out. | The driver has to do it, and people forget, especially on exit. Plain stickers can be copied (that's why we use secure tags plus the phone's location at tap time). | 7, plus the billing rule below |
| 7 | **Driver confirm in the app**: "Did you just arrive?" / "Did you leave?" (built) | Resolves the uncertain middle (0.60–0.95). In the replay: 93% auto + 4% by tapping = 97% confirmed, 0 false confirms. | Drivers ignore notifications. | 8 |
| 8 | **Attendant**: scan booking QR / booking ID / plate, walk-ins, "Needs eyes" list (built; works offline) | The last resort that always works; also catches walk-ins and disputes. | Costs staff time, and humans make mistakes. That's why it comes last, for the few cases the steps above didn't settle. | — |
| + | **Spot camera** (optional, built): overhead phone, occupied/vacant per spot | Exits at **open lots with no gate** (a car leaving its spot), and occupancy. | No identity. Cars hide each other. Needs a high mount. | 4–8 |

## Billing rule that makes people tap (planned)

- **Tapping out stops the clock.** An evidence, driver or attendant check-out is billed to that minute.
- **An inferred check-out** (nobody saw the car leave) is billed to the **later of** the booking end and the inferred time.
- **Points:** bonus points for tapped arrivals and departures, plus streaks (`config/rewards.yaml`). These are a sweetener only; the billing rule is the incentive that actually works.

## What each kind of lot uses

| Lot | Check-in | Check-out | Until cameras are installed |
|---|---|---|---|
| Gated / mall (BDD, LAU) | Sensors + plate camera; car-look match if the plate was not read | Sensors + plate camera | Sensors count cars; phone signals + NFC sticker + driver tap say who |
| Big valet (HAMRA) | Camera at the **drop-off stand**; the valet scans the booking QR at key handover | "Tap to call your car" (NFC or in-app) → the valet hands over the car → check-out | Valet scan + "call your car" |
| Open lot, no gate (AUB) | Phone signals + secure NFC sticker; spot camera if mounted | NFC tap-out or in-app "I left" (stops the clock); spot camera; else inferred and billed to booking end | Same |
| Small partner lot | Phone signals + NFC sticker + driver tap | NFC tap-out / "I left" | Same |

**In short:** where there is no camera, the phone does the automatic part and the secure NFC sticker is the
confirmation (billing rewards the tap). Where there's a gate, the sensors make departures exact. The camera
adds who it is, and the car-look match and the attendant cover the cars the camera misses.

## Cheating the exit fee

| What they do | What the system sees | Result |
|---|---|---|
| Leave the lot and park on the street | Exit sensors + plate | Checked out correctly; they really left |
| Leave and come back in | Exit, then a new entry + plate | A new stay, billed |
| Move to another spot inside the lot | No gate crossing | The stay keeps running (billing is per stay, not per spot) |
| Leave an open lot without tapping | Weak GPS only | Inferred check-out, **billed to booking end** |
