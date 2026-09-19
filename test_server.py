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

    def send_raw(self, commands, gap=0.1, read_seconds=1.5, reset_input=True):
        self.commands.append(("send_raw", tuple(commands), gap, read_seconds, reset_input))
        return [
            {"at_ms": 0.0, "kind": "write", "command": commands[0]},
            {"at_ms": 118.4, "kind": "read", "data": b"ok\r\n"},
        ]


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


# --- raw capture endpoint ------------------------------------------------
#
# The endpoint writes arbitrary commands to the device, so the flag being off
# by default is part of its contract, not a detail.


@pytest.fixture
def raw_enabled(monkeypatch):
    monkeypatch.setenv("HDMI_MATRIX_ENABLE_RAW_COMMAND", "1")


def test_raw_capture_is_absent_unless_the_flag_is_set(client, monkeypatch):
    monkeypatch.delenv("HDMI_MATRIX_ENABLE_RAW_COMMAND", raising=False)
    response = client.post("/debug/raw-command", json={"commands": ["sta"]})
    assert response.status_code == 404


def test_raw_capture_returns_the_bytes_and_their_arrival_times(client, matrix, raw_enabled):
    response = client.post("/debug/raw-command", json={"commands": ["spoasi02", "spobsi04"]})
    assert response.status_code == 200
    assert matrix.commands == [("send_raw", ("spoasi02", "spobsi04"), 0.1, 1.5, True)]
    assert response.json()["events"] == [
        {"at_ms": 0.0, "kind": "write", "command": "spoasi02"},
        {"at_ms": 118.4, "kind": "read", "bytes": 4, "text": "ok\\r\\n", "base64": "b2sNCg=="},
    ]


def test_raw_capture_strips_spaces_from_commands(client, matrix, raw_enabled):
    client.post("/debug/raw-command", json={"commands": ["spob copy outa on"]})
    assert matrix.commands[0][1] == ("spobcopyoutaon",)


def test_raw_capture_invalidates_the_cached_status(client, matrix, raw_enabled):
    client.get("/status")
    client.post("/debug/raw-command", json={"commands": ["spoasi02"]})
    client.get("/status")
    assert matrix.status_reads == 2


@pytest.mark.parametrize("command", ["spcrsb3", "SPC DF"])
def test_raw_capture_refuses_commands_needing_physical_access_to_undo(
    client, matrix, raw_enabled, command
):
    response = client.post("/debug/raw-command", json={"commands": [command]})
    assert response.status_code == 400
    assert matrix.commands == []


def test_raw_capture_refuses_to_hold_the_port_too_long(client, matrix, raw_enabled):
    response = client.post("/debug/raw-command", json={"commands": ["sta"], "read_seconds": 60})
    assert response.status_code == 400
    assert matrix.commands == []
