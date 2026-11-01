# Demo script: 5 minutes

One command sets everything up and plays a scripted Monday morning (16 Nov 2026) on the demo clock.
The terminal prints one line per event: that is your script. The dashboard shows the same events in the
**Demo** banner at the top, so the audience can follow on the projector.

```bash
uv run python -m tools.demo
```

What it does:
1. Generates the data and runs Rhythm if they are missing (first run: a few minutes; `--fresh` redoes it).
2. Starts the API on :8000 and the dashboard on :5173. Logs go to `runs/demo_api.log` and `runs/demo_dashboard.log`.
3. Opens http://localhost:5173/?demo=1#lots.
4. Empties the lots and replays the five story cars at 30× speed. Quiet stretches are skipped (`--max-gap`, default 6 s), so the morning takes about 1½ minutes.
5. Leaves the servers running for questions. **Ctrl+C** stops both, with their child processes, on Windows too.

| Flag | Use |
|---|---|
| `--speed 15` | Slower, if you talk over every event (default 30). |
| `--full` | The whole morning's traffic: about 100 other bookings fill the lots. Story cars are still narrated, and the other check-ins are summarised (+10 …). |
| `--no-open` | Don't open a browser tab, e.g. when the projector browser is already open. |
| `--api-port` / `--dash-port` | When 8000/5173 are taken. |
| `--no-servers` | The API is already running on `--api-port`: only replay. |
| `--exit` | Stop the servers when the replay ends. |

Rerun the command to start the morning again: it resets the lots each time.

## Before you present (10 min before)
- [ ] Run `uv run python -m tools.demo --exit` once on the presenting laptop. This warms the data, Rhythm run and `npm install`, and proves both ports are free.
- [ ] Browser at 110–125% zoom, light theme (projectors wash out dark), one tab: `localhost:5173/?demo=1#lots`.
- [ ] Terminal font large, placed next to the browser or on your own screen. It holds your lines.
- [ ] Optional: phone with the attendant app on the same Wi-Fi (`apps/mobile/.env` → laptop IP).
- [ ] The backup video is on the desktop (see the fallback section).

## The five minutes

**0:00–0:40 · The problem (no clicks).**
"Lots don't know who is actually parked. Attendants check plates by hand, and drivers forget to check in or out.
GoPark Sense works it out from evidence the lot and the phone already have, and it learns each driver's routine
to know who to expect."

**0:40 · Start.** Run `uv run python -m tools.demo` (or press Enter on the prepared command). Switch to the browser on **Live lots**, lot **AUB**.

**0:45–1:15 · ① Clean automatic check-in (AUB).**
Terminal: *geofence enter → ARRIVING 63% → plate camera reads it → CHECKED IN 99%*.
Say: "Phone geofence alone gets 63%, between our two thresholds. The gate camera reading the plate pushes it past 95%,
so it checks in with nobody touching anything." Point at the banner and the **Check-ins today: auto** pill.

**1:15–1:35 · ⑤ arrives (LAU).** "This car parks at LAU. Remember it; it comes back at the end."

**1:35–2:20 · ④ Unknown car, attendant resolves (AUB).**
Terminal: *gate sensor: a car drove in → unidentified car counted → ALERT low confidence*.
Say: "Dead phone, mud on the plate. The break-beam sensor at the gate still counts it." Point at the **Occupied** tile:
*incl. 1 unidentified (gate sensor)*. Point at the flagged alert in **Alerts**.
Then: *attendant scans booking → CHECKED IN by attendant → placeholder closed*. "The attendant scans it once,
the unknown car becomes this booking, and the alert closes itself."
*(If the phone is set up: do this scan live on the attendant app instead, with the booking ID from the ④ line in the terminal: B016531 with the default data.)*

**2:20–2:50 · ③ Weak GPS: ask the driver (AUB).**
Terminal: *geofence ±376 m → ARRIVING 93% → driver taps "Yes, I parked" → CHECKED IN*.
Say: "93% isn't enough to charge someone. Between 60 and 95% we ask the driver with one tap instead of guessing."

**2:50–3:30 · ② Underground (ACHRAFIEH): the evidence.**
Terminal: *geofence → 63% → ramp camera reads plate → CHECKED IN 99% → phone's motion arrives late (offline)*.
Click **Detection log** → lot ACHRAFIEH → the plate (or click it in the banner). Show the probability line
crossing the dashed **ask driver 60%** and **auto-confirm 95%** guides, with one row per signal source.
Say: "Every decision is explainable: each signal's weight, and how its freshness decays. Here the camera decided it."

**3:30–4:00 · ⑤ Overstay with a fee (LAU).**
Terminal: *booking ended → ALERT overstay … $1.00 → update … $2.00 → spot free, plate exit → CHECKED OUT auto*.
Back on **Live lots**, lot LAU. Say: "The spot camera still saw the car after its booking ended, so instead of
silently checking it out we flag the overstay with an estimated fee. When it leaves, the cameras close the stay."

**4:00–4:40 · Rhythm (C2).** Click **Evaluation**.
Hit@1 bars: "Rhythm predicts the next booking better than the three baselines." Then the per-persona chart and the
routine-recovery tiles: "We're honest about where the baseline still wins: day-set recovery for switchers and new users."
If time allows: **Booking patterns** → click a user → the heatmap and routine bands.

**4:40–5:00 · Close.** "One loop: Rhythm predicts who's coming, fusion confirms who came, and attendants handle
only what's uncertain." Detection accuracy over all 674 replayed traces is on the slides
(`uv run python -m tools.simulator evaluate` → `runs/fusion_metrics.json`). The Evaluation page's
*Detection accuracy* section fills in once the API serves that file.

## Fallbacks
| If… | Do |
|---|---|
| **Wi-Fi is down** | Nothing changes: API, dashboard and replay are all on `localhost`. Only the phone needs the network: skip it, the replay does the attendant scan. |
| **The phone app fails** | Say "the attendant scans it" and let the replay do it (the step happens 4 min of demo time after the gate event). |
| **The dashboard won't load** | The terminal narration alone tells the story. Or start the API only with `--no-dashboard` and show http://localhost:8000/docs. |
| **Port in use** | `uv run python -m tools.demo --api-port 8010 --dash-port 5180` and open the printed URL. |
| **Something hangs mid-story** | Ctrl+C, rerun the command: it resets the lots and starts over in seconds (data and Rhythm are reused). |
| **Laptop dies / nothing runs** | Play the backup video, then show the Evaluation numbers from the slides. |
| **Evaluation page empty** | `uv run python -m rhythm evaluate`, then reload. |

## Questions you may get
- *Why not camera only?* Cameras miss plates (mud, glare, motorbikes). The phone, beacon and gate sensor each add evidence. Fusion degrades gracefully, and the Detection log shows why.
- *What if fusion is wrong?* Between 60% and 95% it asks the driver. Below 60% after 10 min, it alerts the attendant. A contradiction (plate seen at two lots) is never auto-confirmed.
- *Privacy?* Plates are matched against bookings at that lot only. Phone signals are opt-in, and geofence events carry no raw track.
