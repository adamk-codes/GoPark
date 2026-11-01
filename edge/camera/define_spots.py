"""Click-to-define spot polygons on one frame of the overhead camera; writes the spots JSON.

    uv run python -m edge.camera.define_spots --source http://PHONE_IP:8080/shot.jpg --lot-id LAU --out edge/spots.json

Left click: add a corner.  Enter / right click: close the polygon (named <LOT>-01, <LOT>-02, ... or typed
in the console with --ask-names).  u: undo last point / polygon.  s: save.  q / Esc: save and quit.
The frame is only shown on screen, never saved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from edge.camera.common import frames


def main(argv: list[str] | None = None) -> None:
    import cv2
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="stream URL, webcam index, video file, image or folder")
    ap.add_argument("--lot-id", required=True)
    ap.add_argument("--out", default="edge/spots.json")
    ap.add_argument("--start", type=int, default=1, help="first spot number")
    ap.add_argument("--ask-names", action="store_true", help="type each space_id in the console")
    a = ap.parse_args(argv)

    img = next(iter(frames(a.source)), (None, None))[1]
    if img is None:
        raise SystemExit(f"no frame from {a.source}")
    h, w = img.shape[:2]
    out = Path(a.out)
    spots: list[dict] = []
    if out.exists():
        spots = json.loads(out.read_text(encoding="utf-8")).get("spots", [])
        print(f"loaded {len(spots)} existing spot(s) from {out}")
    current: list[list[int]] = []

    def close():
        nonlocal current
        if len(current) < 3:
            print("need >= 3 points")
            return
        sid = f"{a.lot_id}-{a.start + len(spots):02d}"
        if a.ask_names:
            sid = input(f"space_id [{sid}]: ").strip() or sid
        spots.append({"space_id": sid, "polygon": current})
        print(f"added {sid}: {current}")
        current = []

    def on_mouse(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN:
            current.append([int(x), int(y)])
        elif ev == cv2.EVENT_RBUTTONDOWN:
            close()

    def save():
        cfg = {"lot_id": a.lot_id, "frame_size": [w, h], "spots": spots}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"saved {len(spots)} spot(s) -> {out}")

    win = "define_spots: click corners, Enter=close, u=undo, s=save, q=quit"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        vis = img.copy()
        for s in spots:
            p = np.array(s["polygon"], np.int32)
            cv2.polylines(vis, [p], True, (0, 200, 0), 2)
            cv2.putText(vis, s["space_id"], tuple(p[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 0), 2)
        if current:
            cv2.polylines(vis, [np.array(current, np.int32)], False, (0, 165, 255), 2)
            for x, y in current:
                cv2.circle(vis, (x, y), 4, (0, 165, 255), -1)
        cv2.imshow(win, vis)
        k = cv2.waitKey(30) & 0xFF
        if k in (13, 10):
            close()
        elif k == ord("u"):
            if current:
                current.pop()
            elif spots:
                print(f"removed {spots.pop()['space_id']}")
        elif k == ord("s"):
            save()
        elif k in (ord("q"), 27):
            save()
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
