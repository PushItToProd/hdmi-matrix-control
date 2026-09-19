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

    def send_commands(self, commands):
        self.commands.append(tuple(commands))
        return 'ok'

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
    assert first["outputs"] == second["outputs"]
    assert first["observed_at"] == second["observed_at"]
    assert first["version"] == second["version"]
    assert second["age_ms"] >= first["age_ms"]
    assert matrix.status_reads == 1


def test_switching_an_output_invalidates_the_cached_status(client, matrix):
    client.get("/status")
    assert matrix.status_reads == 1

    resp = client.post("/set-output-input", json={"output": "B", "input": 4})
    assert resp.status_code == 200
    assert matrix.commands == [("SPOBSI04",)]

    client.get("/status")
    assert matrix.status_reads == 2


def test_setting_output_a_mode_invalidates_the_cached_status(client, matrix):
    client.get("/status")
    resp = client.post("/set-output-a-mode", json={"mode": "pip", "main": 1, "small": 4})
    assert resp.status_code == 200
    assert matrix.commands == [("SPOAPIP14",)]

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


@pytest.mark.parametrize('body, commands', [
    ({'A': 2, 'B': 4}, ('SPOASI02', 'SPOBSI04')),
    ({'A': {'mode': 'single', 'input': 2}, 'B': {'input': 4}}, ('SPOASI02', 'SPOBSI04')),
    ({'A': {'mode': '2x2', 'combination': 1}}, ('SPOA2X21',)),
    ({'A': {'mode': '1b3s', 'combination': 2}}, ('SPOA1B3S2',)),
    ({'A': {'mode': '2plr', 'left': 1, 'right': 4}, 'B': 1}, ('SPOA2PLR14', 'SPOBSI01')),
    ({'A': {'mode': '2pud', 'top': 1, 'bottom': 4}}, ('SPOA2PUD14',)),
    ({'A': {'mode': 'pip', 'main': 1, 'small': 4}}, ('SPOAPIP14',)),
    ({'B': 1}, ('SPOBSI01',)),
])
def test_apply_normalizes_routes_into_one_batch(client, matrix, body, commands):
    before = client.get('/status').json()
    response = client.post('/apply', json=body)
    assert response.status_code == 200, response.text
    assert matrix.commands == [commands]
    after = client.get('/status').json()
    assert matrix.status_reads == 2
    assert after['version'] > before['version']


@pytest.mark.parametrize('body', [
    {}, {'A': None}, {'A': 0}, {'A': 5}, {'B': 0}, {'B': 5},
    {'A': True}, {'B': True}, {'A': '2'}, {'B': 1.5},
    {'A': {'mode': 'pip', 'main': 1}},
    {'A': {'mode': 'single', 'input': 1, 'left': 2}},
    {'A': {'mode': 'single', 'input': True}},
    {'A': 1, 'B': {'input': 4, 'copy_a': True}},
    {'A': 1, 'B': {'input': 4, 'copy_a': False}},
    {'A': 1, 'extra': 4}, {'A': 1, 'if_version': -1},
])
def test_invalid_apply_never_partially_writes(client, matrix, body):
    before = client.get('/status').json()
    assert client.post('/apply', json=body).status_code in (400, 422)
    assert matrix.commands == []
    assert client.get('/status').json()['version'] == before['version']
    assert matrix.status_reads == 1


def test_apply_version_conflict_sends_nothing(client, matrix):
    before = client.get('/status').json()
    body = {'A': 2, 'B': 4, 'if_version': before['version']}
    assert client.post('/apply', json=body).status_code == 200
    assert client.post('/apply', json=body).status_code == 409
    assert len(matrix.commands) == 1


@pytest.mark.parametrize('exception, code', [
    (server.CommandTimeout('missing B'), 504),
    (server.CommandRejected('unknown command'), 502),
    (OSError('port lost'), 500),
])
def test_uncertain_command_invalidates_cached_state(client, matrix, exception, code):
    before = client.get('/status').json()
    def fail(commands):
        raise exception
    matrix.send_commands = fail
    assert client.post('/apply', json={'B': 4}).status_code == code
    after = client.get('/status').json()
    assert after['version'] > before['version']
    assert matrix.status_reads == 2


def test_legacy_quick_query_is_ignored_and_reply_is_drained(client, matrix):
    response = client.post('/set-output-input?quick=true', json={'output': 'B', 'input': 4})
    assert response.status_code == 200
    assert response.json()['response'] == 'ok'
    assert matrix.commands == [('SPOBSI04',)]


def test_status_max_age_preserves_display_cache_and_refreshes_explicitly(matrix):
    from test_status_cache import FakeClock
    clock = FakeClock()
    cache = StatusCache(matrix.get_status, ttl=server.STATUS_CACHE_TTL, clock=clock)
    server.app.dependency_overrides[server.get_status_cache] = lambda: cache
    try:
        client = TestClient(server.app)
        first = client.get('/status').json()
        for _ in range(4):
            clock.advance(1)
            assert client.get('/status').json()['observed_at'] == first['observed_at']
        assert matrix.status_reads == 1
        assert client.get('/status?max_age=0.2').status_code == 200
        assert matrix.status_reads == 2
        clock.advance(.3)
        assert client.get('/status?max_age=0.2').status_code == 200
        assert matrix.status_reads == 3
        assert client.get('/status').status_code == 200
        assert matrix.status_reads == 3
    finally:
        server.app.dependency_overrides.clear()


@pytest.mark.parametrize('age', ['-1', 'nan', 'inf'])
def test_status_rejects_invalid_freshness_budget(client, matrix, age):
    assert client.get('/status', params={'max_age': age}).status_code == 422
    assert matrix.status_reads == 0
