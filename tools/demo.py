"""One-command demo: data + Rhythm run + API + dashboard, then a scripted morning replayed on the demo clock.

    uv run python -m tools.demo                 # story cars only, 30x, opens the dashboard
    uv run python -m tools.demo --full          # the whole morning's traffic, story cars narrated
    uv run python -m tools.demo --speed 60 --no-open --api-port 8003 --dash-port 5174

Every story event prints one narration line: that is the presenter's script (docs/DEMO.md).
Ctrl+C stops the API and the dashboard (their whole process trees, on Windows too).
"""
from __future__ import annotations

import argparse
import atexit
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RUNS = ROOT / "runs"
DAY = "2026-11-16"
MORNING = (f"{DAY}T07:00:00+02:00", f"{DAY}T09:30:00+02:00")   # bookings starting in this window
EXIT_KINDS = {"exit", "unpark", "vacant"}
CIRCLED = "①②③④⑤⑥"


# ---------------------------------------------------------------- child processes
class Children:
    """API + dashboard processes; stopped with their whole tree on exit, Ctrl+C or Ctrl+Break."""

    def __init__(self):
        self.procs: list[tuple[str, subprocess.Popen]] = []
        atexit.register(self.stop)

    def start(self, name: str, cmd: list[str], log: Path, env: dict | None = None) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        out = open(log, "w", encoding="utf-8")
        kw: dict = {"cwd": ROOT, "stdout": out, "stderr": subprocess.STDOUT, "env": {**os.environ, **(env or {})}}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # our Ctrl+C doesn't reach it; we kill the tree
        else:
            kw["start_new_session"] = True
        self.procs.append((name, subprocess.Popen(cmd, **kw)))

    def stop(self) -> None:
        while self.procs:
            name, p = self.procs.pop()
            if p.poll() is not None:
                continue
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            else:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
            print(f"  stopped {name}")

    def check(self) -> None:
        for name, p in self.procs:
            if p.poll() is not None:
                raise SystemExit(f"{name} exited with code {p.returncode}; see runs/demo_{name}.log")


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_http(url: str, children: Children, timeout: float = 120) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        children.check()
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {url}")


def run_step(title: str, cmd: list[str]) -> None:
    print(f"- {title}: {' '.join(cmd[2:] if cmd[0] == sys.executable else cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def prepare(args) -> None:
    """Generate data and run Rhythm when missing (or always with --fresh)."""
    if args.fresh or not (DATA / "bookings.csv").exists() or not (DATA / "traces" / "index.csv").exists():
        run_step("generating data", [sys.executable, "-m", "tools.generator", "--users", "300", "--weeks", "26", "--seed", "42"])
    if args.fresh or not (RUNS / "latest.txt").exists():
        run_step("running Rhythm", [sys.executable, "-m", "rhythm", "run"])
        run_step("evaluating Rhythm", [sys.executable, "-m", "rhythm", "evaluate"])


def start_servers(args, children: Children) -> str:
    api = f"http://127.0.0.1:{args.api_port}"
    for port, flag in ((args.api_port, "--api-port"), (args.dash_port, "--dash-port")):
        if port_busy(port):
            raise SystemExit(f"port {port} is in use: stop that server, pick another {flag}, or use --no-servers")
    children.start("api", [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", str(args.api_port)],
                   RUNS / "demo_api.log")
    if not args.no_dashboard:
        npm = shutil.which("npm")
        if npm is None:
            raise SystemExit("npm not found: install Node.js, or run with --no-dashboard")
        if not (ROOT / "dashboard" / "node_modules").exists():
            run_step("installing dashboard packages", [npm, "--prefix", "dashboard", "install"])
        children.start("dashboard", [npm, "--prefix", "dashboard", "run", "dev", "--", "--port", str(args.dash_port), "--strictPort"],
                       RUNS / "demo_dashboard.log", env={"GOPARK_API": f"http://localhost:{args.api_port}"})
    print(f"- starting API on :{args.api_port}" + ("" if args.no_dashboard else f" and dashboard on :{args.dash_port}"))
    wait_http(f"{api}/health", children)
    if not args.no_dashboard:
        wait_http(f"http://127.0.0.1:{args.dash_port}/", children)
    return api


# ---------------------------------------------------------------- the story
@dataclass
class Story:
    n: int
    key: str
    title: str
    pitch: str
    trace: object = None           # tools.simulator.timeline.Trace
    actions: list = field(default_factory=list)   # (at, kind, payload)


STORIES = [
    Story(1, "clean", "Clean automatic check-in",
          "phone geofence + motion + the gate's plate camera: confirmed with nobody lifting a finger"),
    Story(2, "underground", "Underground car, confirmed by camera",
          "GPS drops at the ramp, no beacon, the phone is offline below ground: the ramp's plate camera decides it"),
    Story(3, "weak_gps", "Weak GPS: ask the driver",
          "only a coarse geofence: fusion is fairly sure but not sure enough, so the app asks the driver (one tap)"),
    Story(4, "unknown", "Unknown car, resolved by the attendant",
          "dead phone and an unreadable plate: the gate sensor still counts the car; the attendant scans it"),
    Story(5, "overstay", "Overstay with a fee",
          "parks now and is still there when the booking ends: the spot camera keeps the stay open → overstay alert with a fee"),
]


def pick_stories(traces: list, cfg: dict, priors: list) -> list[Story]:
    """Choose one trace per story beat by what fusion really does with it (offline emulation), so the
    script still works after the data is regenerated."""
    import copy
    from tools.simulator import evaluate as ev
    outcome = {t.booking_id: ev.emulate(t, cfg, priors) for t in traces}

    def camera_decides(t) -> bool:
        """Confirmed with the plate camera, not without it."""
        blind = copy.copy(t)
        blind.lines = [l for l in t.lines if l["source"] != "camera_plate"]
        return not ev.emulate(blind, cfg, priors)["confirmed_at"]

    has = lambda t, src: any(l["source"] == src for l in t.lines)  # noqa: E731
    gated = {"open", "gated", "underground"}
    rules = {
        "clean": lambda t: t.scenario == "clean" and t.lot_kind == "open" and outcome[t.booking_id]["confirmed_at"],
        "underground": lambda t: t.scenario == "underground" and outcome[t.booking_id]["confirmed_at"]
        and has(t, "camera_plate") and camera_decides(t),
        "weak_gps": lambda t: t.scenario == "weak_gps" and outcome[t.booking_id]["arriving_at"] and not outcome[t.booking_id]["confirmed_at"],
        "unknown": lambda t: t.scenario == "phone_dead" and t.lot_kind in gated and t.arrives,
        "overstay": lambda t: t.scenario == "clean" and has(t, "camera_spot") and outcome[t.booking_id]["confirmed_at"],
    }
    used: set[str] = set()
    out = []
    for s in STORIES:
        pool = [t for t in traces if t.booking_id not in used and rules[s.key](t)]
        if s.key == "overstay":
            pool.sort(key=lambda t: t.end)      # the earliest booking end keeps the demo short
        if s.key in ("clean", "weak_gps"):
            pool.sort(key=lambda t: (t.lot_id != "AUB", t.start))
        if s.key == "underground":
            pool.sort(key=lambda t: (t.variant != "no_ble", t.start))
        if not pool:
            print(f"  (no trace fits story {s.n}: {s.title}; skipped)")
            continue
        s.trace = pool[0]
        used.add(s.trace.booking_id)
        out.append(s)
    return out


def script(stories: list[Story]) -> None:
    """Bend the chosen traces into the story beats and add the attendant's actions."""
    from tools.simulator.timeline import parse
    for s in stories:
        t = s.trace
        if s.key in ("underground", "weak_gps", "unknown"):
            # The morning is about arrivals; these cars stay parked past the end of the demo.
            t.lines = [l for l in t.lines if l["kind"] not in EXIT_KINDS]
        if s.key == "weak_gps":
            # Fusion stops in the ask-driver band; two minutes later the driver taps "yes, I parked".
            first = min(parse(l["observed_at"]) for l in t.lines)
            t.lines.append({**_line(t, "driver_confirm", "confirm", first + timedelta(minutes=2), {"answer": "yes"},
                                    device=f"dev-{t.user_id}"), "user_id": t.user_id, "booking_id": t.booking_id})
        if s.key == "unknown":
            # The plate camera fails (mud on the plate); only the gate's presence sensor sees the car.
            t.lines = [l for l in t.lines if l["source"] not in ("camera_plate", "gate_sensor")]
            at = t.actual_arrival
            t.lines.append(_gate(t, "enter", at))
            if t.actual_departure:
                t.lines.append(_gate(t, "exit", t.actual_departure))
            scan_at = max(at + timedelta(minutes=4), t.start - timedelta(minutes=25))
            s.actions.append((scan_at, "scan", {"lot_id": t.lot_id, "booking_id": t.booking_id}))
        if s.key == "overstay":
            # The car stays past the booking end; it leaves 40 min late and the cameras see it go.
            t.lines = [l for l in t.lines if l["kind"] not in EXIT_KINDS]
            leave = t.end + timedelta(minutes=40)
            space = next((l["payload"].get("space_id") for l in t.lines if l["source"] == "camera_spot"), None)
            if space:
                t.lines.append(_line(t, "camera_spot", "vacant", leave, {"space_id": space}, device=f"cam-{t.lot_id}-overhead"))
            t.lines.append(_line(t, "camera_plate", "exit", leave, {"plate_norm": t.plate_norm, "ocr_conf": 0.93,
                                                                   "camera": "gate", "direction": "out"},
                                 device=f"cam-{t.lot_id}-gate"))
            t.lines.append(_gate(t, "exit", leave))
            for extra in (11, 26):     # let the time rules see the overstay at two fee steps
                s.actions.append((t.end + timedelta(minutes=extra), "tick", {}))
        t.lines.sort(key=lambda l: parse(l["observed_at"]))


def _line(t, source, kind, at: datetime, payload: dict, device: str) -> dict:
    return {"client_event_id": f"demo-{uuid.uuid4()}", "device_id": device, "user_id": None, "lot_id": t.lot_id,
            "booking_id": None, "source": source, "kind": kind, "observed_at": at.isoformat(timespec="seconds"),
            "payload": payload, "truth": True}


def _gate(t, kind: str, at: datetime) -> dict:
    return _line(t, "gate_sensor", kind, at, {"gate_id": f"{t.lot_id}-1", "lane": 1}, device=f"gate-{t.lot_id}-1")


# ---------------------------------------------------------------- narration
def describe(sig: dict, plate: str | None) -> str:
    src, kind, p = sig["source"], sig["kind"], sig.get("payload") or {}
    lot = sig["lot_id"]
    who = f"{plate}'s phone" if plate else "the driver's phone"
    late = " (uploaded late: the phone was offline underground)" if p.get("sent_at") else ""
    text = {
        ("geofence", "enter"): f"{who} enters the {lot} geofence" + (f" (±{p['accuracy_m']} m)" if p.get("accuracy_m") else ""),
        ("geofence", "exit"): f"{who} leaves the {lot} geofence",
        ("motion", "park"): f"{who}: driving → walking, parked",
        ("motion", "unpark"): f"{who}: walking → driving",
        ("ble", "enter"): f"{who} hears the {lot} beacon" + (f" (RSSI {p['rssi']})" if p.get("rssi") else ""),
        ("camera_plate", "enter"): f"plate camera at the {lot} {p.get('camera', 'gate')} reads {p.get('plate_norm')}"
                                   + (f" (OCR {p['ocr_conf']:.0%})" if p.get("ocr_conf") else ""),
        ("camera_plate", "exit"): f"plate camera sees {p.get('plate_norm')} leave {lot}",
        ("camera_spot", "occupied"): f"spot camera: {p.get('space_id', 'a spot')} is now occupied",
        ("camera_spot", "vacant"): f"spot camera: {p.get('space_id', 'the spot')} is free again",
        ("gate_sensor", "enter"): f"gate sensor {p.get('gate_id', lot)}: a car drove in",
        ("gate_sensor", "exit"): f"gate sensor {p.get('gate_id', lot)}: a car drove out",
        ("driver_confirm", "confirm"): f"the driver taps “Yes, I parked” in the app",
    }.get((src, kind), f"{src} {kind} at {lot}")
    return text + late


def pct(p) -> str:
    return "—" if p is None else f"{p:.1%}" if 0.995 <= p < 1 else f"{p:.0%}"


class Narrator:
    """Prints a line per story signal, then what the API decided (stay state changes, new alerts)."""

    def __init__(self, client, stories: list[Story]):
        self.client = client
        self.by_booking = {s.trace.booking_id: s for s in stories}
        self.by_signal = {l["client_event_id"]: s for s in stories for l in s.trace.lines}
        self.unknown = next((s for s in stories if s.key == "unknown"), None)
        self.unknown_ids: set[str] = set()     # placeholder stays (G-…) that belong to story 4
        self.stays: dict[str, dict] = {}
        self.alerts: dict[int, str] = {}   # id -> what was last narrated (message, or fee for overstays)
        self.started: set[int] = set()
        self.background = 0
        self.reported = 0

    def say(self, at: datetime, lot: str, text: str, mark: str = " ") -> None:
        print(f"[{at.strftime('%H:%M')}] {lot:<9} {mark} {text}", flush=True)

    def intro(self, s: Story) -> None:
        if s.n in self.started:
            return
        self.started.add(s.n)
        t = s.trace
        print(f"\n{CIRCLED[s.n - 1]} {s.title} · {t.lot_id} ({t.lot_kind}) · booking {t.booking_id} "
              f"{t.start.strftime('%H:%M')}–{t.end.strftime('%H:%M')} · plate {t.plate_norm}\n   {s.pitch}", flush=True)

    def story_for(self, sig: dict) -> Story | None:
        return self.by_signal.get(sig["client_event_id"])

    def _is_unknown(self, st: dict) -> bool:
        """A booking-less gate placeholder at story 4's lot, within minutes of its car."""
        if st["id"] in self.unknown_ids:
            return True
        t = self.unknown.trace if self.unknown else None
        if not t or st["booking_id"] is not None or not st["id"].startswith("G-") or st["lot_id"] != t.lot_id or not st["in_at"]:
            return False
        from tools.simulator.timeline import parse
        if abs((parse(st["in_at"]) - t.actual_arrival).total_seconds()) <= 120:
            self.unknown_ids.add(st["id"])
            return True
        return False

    def observe(self, at: datetime) -> None:
        """Diff stays and alerts against the last look and narrate what changed."""
        stays = self.client.get("/stays").json()
        for st in stays:
            prev = self.stays.get(st["id"])
            self.stays[st["id"]] = st
            if prev and (prev["state"], prev["in_conf"], prev["out_at"]) == (st["state"], st["in_conf"], st["out_at"]):
                continue
            s = self.by_booking.get(st["booking_id"])
            if s and st["state"] == "UNCONFIRMED_DEPARTURE" and s.key != "overstay":
                continue
            if s is None and not self._is_unknown(st):
                if prev is None and st["state"] == "CHECKED_IN":
                    self.background += 1
                continue
            text = (f"{st['plate_norm']} CHECKED IN: the driver confirmed" if s and s.key == "weak_gps" and st["state"] == "CHECKED_IN"
                    else self.decision(st, prev))
            self.say(at, st["lot_id"], text, "→")
        for a in self.client.get("/alerts").json():
            if a["type"] in ("upcoming_arrival", "no_show"):
                continue
            # Overstay messages change every minute; only a new fee is news.
            key = f"${a['estimated_fee']}" if a["type"] == "overstay" else a["message"]
            if self.alerts.get(a["id"]) == key:
                continue
            update = a["id"] in self.alerts
            self.alerts[a["id"]] = key
            story = self.by_booking.get(a["booking_id"]) or next(
                (s for s in self.by_booking.values() if a["stay_id"] and a["stay_id"] == f"S-{s.trace.booking_id}"), None)
            if a["type"] == "unconfirmed_departure" and (not story or story.key != "overstay"):
                continue
            if story or a["stay_id"] in self.unknown_ids:
                self.say(at, a["lot_id"], f"{'update' if update else 'ALERT'} {a['type'].replace('_', ' ')}: {a['message']}", "!")

    @staticmethod
    def decision(st: dict, prev: dict | None) -> str:
        plate = st["plate_norm"] or "an unidentified car"
        state = st["state"]
        if st["id"].startswith("G-"):
            return {"CHECKED_IN": "unidentified car counted in occupancy; attendant alerted",
                    "CANCELLED": "placeholder for the unidentified car closed: it was this booking",
                    "CHECKED_OUT": "unidentified car left"}.get(state, f"unidentified car: {state}")
        return {
            "ARRIVING": f"fusion: {plate} ARRIVING, P {pct(st['in_conf'])} (60–95%: ask the driver)",
            "CHECKED_IN": f"{plate} CHECKED IN by {st['in_method']}" + (f" (P {pct(st['in_conf'])})" if st["in_method"] == "auto" else ""),
            "UNCONFIRMED_DEPARTURE": f"{plate}'s booking has ended; departure not confirmed yet",
            "OVERSTAY": f"{plate} is overstaying",
            "CHECKED_OUT": f"{plate} CHECKED OUT by {st['out_method']} at {(st['out_at'] or '')[11:16]}"
                           + (f" (P {pct(st['out_conf'])})" if st["out_method"] == "auto" else ""),
        }.get(state, f"{plate}: {state}")


# ---------------------------------------------------------------- replay
def play(api: str, args) -> None:
    import httpx
    from api.ops import fusion
    from api.rhythm_api.store import latest_priors
    from tools.simulator.timeline import add_gate_sensors, build_timeline, load_traces, parse, send_time

    traces = load_traces(DATA, since=parse(MORNING[0]), until=parse(MORNING[1]))
    stories = pick_stories(traces, fusion.CONFIG, latest_priors())
    story_ids = {s.trace.booking_id for s in stories}
    background = [t for t in traces if t.booking_id not in story_ids] if args.full else []
    unknown = next((s.trace for s in stories if s.key == "unknown"), None)
    if unknown and unknown.actual_arrival:
        # Another booked car checking in at that lot within the claim window would "identify" story 4's car
        # before the attendant does (CONTRACT §6); keep that gate quiet around it.
        near = lambda t: t.lot_id == unknown.lot_id and t.actual_arrival and abs(t.actual_arrival - unknown.actual_arrival) < timedelta(minutes=20)  # noqa: E731
        background = [t for t in background if not near(t)]
    add_gate_sensors(background)
    for s in stories:
        if s.key not in ("unknown", "weak_gps"):
            add_gate_sensors([s.trace])
    script(stories)

    # Signal batches by send time (tools.simulator rules), plus the attendant's actions and a few ticks.
    end = max(max(send_time(l) for l in s.trace.lines) for s in stories if s.key == "overstay") \
        if any(s.key == "overstay" for s in stories) else parse(MORNING[1]) + timedelta(hours=1)
    steps: list[tuple[datetime, str, object]] = []
    for step in build_timeline([s.trace for s in stories] + background, tick_min=0):
        if step.at <= end:
            steps.append((step.at, "signals", step.signals))
    for s in stories:
        steps += [(at, kind, payload) for at, kind, payload in s.actions]
        steps += [(at + timedelta(seconds=20), "tick", {}) for at in
                  (send_time(l) for l in s.trace.lines if l["source"] == "gate_sensor") if at <= end]
    steps.sort(key=lambda x: (x[0], x[1] != "signals"))

    client = httpx.Client(base_url=api, timeout=60)
    print("- resetting the demo database (empty lots)")
    client.post("/dev/reset", params={"prepark": "false"}).raise_for_status()
    narrator = Narrator(client, stories)
    first = steps[0][0] - timedelta(minutes=5)
    client.post("/dev/clock", json={"now": first.isoformat()}).raise_for_status()
    print(f"\nScripted morning {DAY}, {len(stories)} story cars"
          + (f" + {len(background)} other bookings" if background else "") + f", {args.speed:g}x\n")
    last = first
    for at, kind, payload in steps:
        gap = (at - last).total_seconds()
        if gap > max(args.max_gap * args.speed, 900):
            print(f"   … {last.strftime('%H:%M')} → {at.strftime('%H:%M')}", flush=True)
            # Move the clock through a long quiet spell in a few hops so time rules and the dashboard keep up.
            for i in range(1, 4):
                client.post("/dev/clock", json={"now": (last + (at - last) * i / 4).isoformat()})
                client.post("/dev/tick")
                time.sleep(args.max_gap / 4 if args.speed > 0 else 0)
        elif args.speed > 0:
            time.sleep(min(args.max_gap, gap / args.speed))
        last = max(last, at)
        client.post("/dev/clock", json={"now": at.isoformat()}).raise_for_status()
        if kind == "signals":
            for sig in payload:
                s = narrator.story_for(sig)
                if s:
                    narrator.intro(s)
                    narrator.say(at, sig["lot_id"], describe(sig, s.trace.plate_norm if sig["source"] != "gate_sensor" else None))
            client.post("/signals", json={"signals": payload}).raise_for_status()
        elif kind == "scan":
            s = next(s for s in stories if s.key == "unknown")
            narrator.intro(s)
            narrator.say(at, payload["lot_id"], f"attendant scans booking {payload['booking_id']} on the attendant app")
            r = client.post("/scan", json={**payload, "device_id": "attendant-demo",
                                            "client_event_id": f"demo-{uuid.uuid4()}", "observed_at": at.isoformat()})
            narrator.say(at, payload["lot_id"], f"scan: {r.json().get('message', r.status_code)}", "→")
        client.post("/dev/tick")
        narrator.observe(at)
        if narrator.background - narrator.reported >= 10:
            narrator.say(at, "", f"(+{narrator.background - narrator.reported} other cars checked in automatically)")
            narrator.reported = narrator.background
    print(f"\nDone: {len(stories)} story cars"
          + (f", {narrator.background} other cars checked in automatically" if args.full else "")
          + f". Demo clock stays at {last.strftime('%H:%M')}.")


# ---------------------------------------------------------------- main
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m tools.demo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--story", action="store_true", help="only the 5 story cars (default)")
    mode.add_argument("--full", action="store_true", help="the whole morning's traffic; story cars still narrated")
    ap.add_argument("--speed", type=float, default=30.0, help="demo seconds per real second (default %(default)s)")
    ap.add_argument("--max-gap", type=float, default=6.0, help="longest real pause in seconds; longer quiet spells are skipped")
    ap.add_argument("--api-port", type=int, default=8000)
    ap.add_argument("--dash-port", type=int, default=5173)
    ap.add_argument("--no-servers", action="store_true", help="use an API already running on --api-port")
    ap.add_argument("--no-dashboard", action="store_true", help="start only the API")
    ap.add_argument("--no-open", action="store_true", help="don't open the dashboard in the browser")
    ap.add_argument("--fresh", action="store_true", help="regenerate data and rerun Rhythm first")
    ap.add_argument("--exit", action="store_true", help="stop the servers when the replay ends (default: keep running)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):      # Windows consoles default to cp1252; the script uses ①→…
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if os.name == "nt" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)   # Ctrl+Break cleans up like Ctrl+C
    children = Children()
    try:
        prepare(args)
        api = f"http://127.0.0.1:{args.api_port}" if args.no_servers else start_servers(args, children)
        dash = f"http://localhost:{args.dash_port}/?demo=1#lots"
        if not args.no_dashboard and not args.no_servers:
            print(f"- dashboard: {dash}")
            if not args.no_open:
                webbrowser.open(dash)
        play(api, args)
        if children.procs and not args.exit:
            print("Servers keep running for questions. Ctrl+C to stop.")
            while True:
                children.check()
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        children.stop()


if __name__ == "__main__":
    main()
