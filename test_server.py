"""
Tests for the HTTP layer over a stub matrix.

The client is built without its context manager on purpose: entering it would
run the app's lifespan, which opens the real serial port.

Run without the project environment:
    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with fastapi \
        --with httpx --with pyserial --with uvicorn \
        pytest -p no:cacheprovider test_server.py
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from hdmi_matrix_status import parse_status
from status_cache import StatusCache

FIXTURES = Path(__file__).parent / "test_fixtures" / "responses"


class StubMatrix:
    """Answers status from a recorded reply and records commands sent."""

    def __init__(self, fixture: str = "00_spobsi01"):
        self.fixture = fixture
        self.status_reads = 0
        self.commands: list[tuple] = []

    def get_status(self):
        self.status_reads += 1
        raw = (FIXTURES / f"{self.fixture}_status.bin").read_bytes().decode()
        return parse_status(raw)

    def set_output_input(self, output, inp, quick=False):
        self.commands.append(("set_output_input", output, inp))
        return None if quick else "ok"

    def set_output_a_pip(self, main, small):
        self.commands.append(("set_output_a_pip", main, small))
        return "ok"


@pytest.fixture
def matrix():
    return StubMatrix()


@pytest.fixture
def client(matrix):
    cache = StatusCache(matrix.get_status, ttl=60.0)
    server.app.dependency_overrides[server.get_matrix] = lambda: matrix
    server.app.dependency_overrides[server.get_status_cache] = lambda: cache
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()


def test_status_reports_the_device_routing(client, matrix):
    body = client.get("/status").json()
    assert body["outputs"]["A"] == {"mode": "single", "input": 4, "resolution": "1080p"}
    assert body["outputs"]["B"]["input"] == 1
    assert body["inputs"]["1"] == {"linked": True}
    assert matrix.status_reads == 1


def test_repeated_status_requests_cost_one_device_read(client, matrix):
    first = client.get("/status").json()
    second = client.get("/status").json()
    assert first == second
    assert matrix.status_reads == 1


def test_switching_an_output_invalidates_the_cached_status(client, matrix):
    client.get("/status")
    assert matrix.status_reads == 1

    resp = client.post("/set-output-input", json={"output": "B", "input": 4})
    assert resp.status_code == 200
    assert matrix.commands == [("set_output_input", "B", 4)]

    client.get("/status")
    assert matrix.status_reads == 2


def test_setting_output_a_mode_invalidates_the_cached_status(client, matrix):
    client.get("/status")
    resp = client.post("/set-output-a-mode", json={"mode": "pip", "main": 1, "small": 4})
    assert resp.status_code == 200
    assert matrix.commands == [("set_output_a_pip", 1, 4)]

    client.get("/status")
    assert matrix.status_reads == 2


def test_a_rejected_command_leaves_the_cache_alone(client, matrix):
    client.get("/status")
    resp = client.post("/set-output-a-mode", json={"mode": "pip", "main": 1})
    assert resp.status_code == 400
    assert matrix.commands == []

    client.get("/status")
    assert matrix.status_reads == 1


def test_status_reports_503_when_the_device_cannot_be_read(matrix):
    def fail():
        raise OSError("serial port went away")

    server.app.dependency_overrides[server.get_matrix] = lambda: matrix
    server.app.dependency_overrides[server.get_status_cache] = lambda: StatusCache(fail, ttl=60.0)
    try:
        resp = TestClient(server.app).get("/status")
        assert resp.status_code == 503
        assert "serial port went away" in resp.json()["error"]
    finally:
        server.app.dependency_overrides.clear()
