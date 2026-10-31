"""GoPark Sense API: ops (Challenge 1) + rhythm (Challenge 2).

    uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import db
from api.ops.routes import refresh_alerts, router as ops_router
from api.rhythm_api.routes import router as rhythm_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    try:
        db.ensure_seeded(conn)
    finally:
        conn.close()
    task = asyncio.create_task(_alert_scheduler())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _alert_scheduler():
    while True:
        conn = db.connect()
        try:
            db.ensure_seeded(conn)
            refresh_alerts(conn)
        finally:
            conn.close()
        await asyncio.sleep(30)


app = FastAPI(title="GoPark Sense API", version="0.1.0-p1", lifespan=lifespan)
# Demo only: the dashboard (Vite) and the Expo app call from other origins on the LAN.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(ops_router)
app.include_router(rhythm_router)
