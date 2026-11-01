"""WS /live?lot_id=: pushes {type: "stay"|"alert", data} whenever a stay or alert changes (CONTRACT.md §7).

Triggers in api/schema.sql append every stay/alert insert or meaningful update to the `changes`
table, whichever code path (scan, fusion, sync, scheduler) made it. Each socket polls that feed
from the id it saw on connect, so changes made by any worker or process reach every client.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api import db
from api.ops.routes import alert_out, stay_out

router = APIRouter()
POLL_S = 0.5


def _pending(conn, cursor: int, lot_id: str | None) -> tuple[int, list[dict]]:
    """Messages for changes after `cursor` (latest state of each entity, in change order) and the new cursor."""
    if conn.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0] < cursor:
        cursor = 0  # /dev/reset recreated the feed
    rows = conn.execute("SELECT * FROM changes WHERE id > ? ORDER BY id", (cursor,)).fetchall()
    if not rows:
        return cursor, []
    latest: dict[tuple[str, str], int] = {}
    for r in rows:
        if lot_id is None or r["lot_id"] == lot_id:
            latest[(r["entity"], r["entity_id"])] = r["id"]
    messages = []
    for (entity, entity_id), _ in sorted(latest.items(), key=lambda item: item[1]):
        if entity == "stay":
            row = conn.execute("SELECT * FROM stays WHERE id = ?", (entity_id,)).fetchone()
            data = stay_out(row) if row else None
        else:
            row = conn.execute("SELECT * FROM alerts WHERE id = ?", (int(entity_id),)).fetchone()
            data = alert_out(row) if row else None
        if data is not None:
            messages.append({"type": entity, "data": data})
    return rows[-1]["id"], messages


@router.websocket("/live")
async def live(websocket: WebSocket, lot_id: str | None = None):
    await websocket.accept()
    conn = db.connect()
    try:
        cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]
        while True:
            cursor, messages = _pending(conn, cursor, lot_id)
            for message in messages:
                await websocket.send_json(message)
            try:
                # Clients may send pings; waiting on receive also notices a disconnect.
                await asyncio.wait_for(websocket.receive_text(), timeout=POLL_S)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        conn.close()
