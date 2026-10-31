"""Test setup: generate a small dataset into a temp folder and point the API at it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contract" / "schemas"


@pytest.fixture(scope="session")
def workdir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("gopark")
    (d / "runs").mkdir()
    os.environ["GOPARK_DATA"] = str(d)
    os.environ["GOPARK_DB"] = str(d / "test.sqlite")
    os.environ["GOPARK_RUNS"] = str(d / "runs")
    subprocess.run([sys.executable, "-m", "tools.generator", "--users", "60", "--weeks", "12",
                    "--seed", "3", "--out", str(d)], cwd=ROOT, check=True, capture_output=True)
    return d


@pytest.fixture(scope="session")
def run_dir(workdir) -> Path:
    from rhythm.pipeline import run
    info = run(workdir / "bookings.csv", out_dir=workdir / "runs")
    return workdir / "runs" / info["run_id"]


@pytest.fixture(scope="session")
def client(workdir, run_dir):
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as c:
        c.post("/dev/reset")
        yield c


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
