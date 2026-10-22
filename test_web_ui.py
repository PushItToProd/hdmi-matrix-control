"""UI behavior over recorded device replies, without opening a serial port."""

import pytest
from fastapi.testclient import TestClient

import server
from status_cache import StatusCache
from test_server import StubMatrix
from test_status_cache import FakeClock


@pytest.fixture
def setup():
    matrix, clock = StubMatrix(), FakeClock()
    cache = StatusCache(matrix.get_status, ttl=5, clock=clock)
    server.app.dependency_overrides[server.get_matrix] = lambda: matrix
    server.app.dependency_overrides[server.get_status_cache] = lambda: cache
    try:
        yield TestClient(server.app), matrix, clock, cache
    finally:
        server.app.dependency_overrides.clear()


def test_page_and_assets_are_available(setup):
    client, matrix, _, _ = setup
    response = client.get('/')
    assert response.status_code == 200
    assert '<title>HDMI matrix</title>' in response.text
    assert 'every 5s' in response.text
    assert 'Output A' in response.text and 'Output B' in response.text
    assert response.headers['cache-control'] == 'no-store'
    for asset in ('htmx.min.js', 'style.css', 'ui.js'):
        assert client.get(f'/ui/static/{asset}').status_code == 200
    assert matrix.status_reads == 1


def test_automatic_updates_and_page_reloads_never_expire_retained_state(setup):
    client, matrix, clock, _ = setup
    client.get('/')
    clock.advance(3600)
    for _ in range(10):
        assert client.get('/ui/state').status_code == 200
        assert client.get('/').status_code == 200
    assert matrix.status_reads == 1


@pytest.mark.parametrize('endpoint,payload,expected', [
    ('/apply', {'A': 2, 'B': 3}, {'A': {'mode': 'single', 'input': 2}, 'B': {'input': 3}}),
    ('/set-output-input', {'output': 'A', 'input': 1}, {'A': {'mode': 'single', 'input': 1}}),
    ('/set-output-input', {'output': 'B', 'input': 4}, {'B': {'input': 4}}),
    ('/set-output-a-mode', {'mode': 'PIP', 'main': 2, 'small': 4}, {'A': {'mode': 'pip', 'main': 2, 'small': 4}}),
])
def test_existing_api_requests_are_remembered_without_device_reads(setup, endpoint, payload, expected):
    client, matrix, _, cache = setup
    assert client.post(endpoint, json=payload).status_code == 200
    assert cache.remembered().requested == expected
    page = client.get('/')
    assert page.status_code == 200
    assert 'not been verified' in page.text
    assert matrix.status_reads == 0


def test_quick_switch_and_layout_use_the_existing_command_path(setup):
    client, matrix, _, cache = setup
    client.get('/')
    assert client.post('/ui/apply', data={'output': 'B', 'input': '3'}).status_code == 200
    assert client.post('/ui/apply', data={'mode': 'pip', 'main': '1', 'small': '4'}).status_code == 200
    assert matrix.commands == [('SPOBSI03',), ('SPOAPIP14',)]
    assert matrix.status_reads == 1
    assert cache.remembered().requested == {'B': {'input': 3}, 'A': {'mode': 'pip', 'main': 1, 'small': 4}}
    assert 'Main input 1' in client.get('/ui/state').text


@pytest.mark.parametrize('mode,fields,command', [
    ('single', {'input': 2}, 'SPOASI02'),
    ('2x2', {'combination': 3}, 'SPOA2X23'),
    ('2plr', {'left': 1, 'right': 4}, 'SPOA2PLR14'),
    ('2pud', {'top': 2, 'bottom': 3}, 'SPOA2PUD23'),
    ('1b3s', {'combination': 2}, 'SPOA1B3S2'),
    ('pip', {'main': 4, 'small': 1}, 'SPOAPIP41'),
])
def test_every_layout_renders_and_submits(setup, mode, fields, command):
    client, matrix, _, _ = setup
    fragment = client.get('/ui/layout', params={'mode': mode})
    assert fragment.status_code == 200
    for field in fields:
        assert f'name="{field}"' in fragment.text
    result = client.post('/ui/apply', data={'mode': mode, **fields})
    assert result.status_code == 200
    assert matrix.commands == [(command,)]
    assert matrix.status_reads == 0


def test_explicit_refresh_has_a_shared_five_second_cooldown(setup):
    client, matrix, clock, cache = setup
    client.get('/')
    client.post('/apply', json={'A': 2})
    for _ in range(4):
        clock.advance(1)
        assert 'limited to once every five seconds' in client.post('/ui/refresh').text
    assert matrix.status_reads == 1
    clock.advance(1)
    assert client.post('/ui/refresh').status_code == 200
    assert matrix.status_reads == 2
    assert cache.remembered().requested == {}
    assert cache.remembered().uncertain is False


def test_failed_reads_share_cooldown_and_do_not_erase_state(setup):
    client, matrix, clock, cache = setup
    client.get('/')
    original = cache.remembered().observation
    calls = []

    def fail():
        calls.append(None)
        raise OSError('disconnected')

    cache._read = fail
    clock.advance(5)
    assert 'Device read failed: disconnected' in client.post('/ui/refresh').text
    for _ in range(3):
        client.post('/ui/refresh')
    assert len(calls) == 1
    assert cache.remembered().observation is original
    assert matrix.status_reads == 1


def test_invalid_selection_sends_nothing(setup):
    client, matrix, _, _ = setup
    for values in ({'output': 'B', 'input': '9'}, {'mode': 'pip', 'main': 1}, {'mode': 'bogus'}):
        result = client.post('/ui/apply', data=values)
        assert 'Invalid selection' in result.text
    assert matrix.commands == []


def test_command_failure_is_visible_and_does_not_trigger_a_read(setup):
    client, matrix, _, cache = setup
    client.get('/')

    def fail(commands):
        raise server.CommandTimeout('No acknowledgment <script>')

    matrix.send_commands = fail
    result = client.post('/ui/apply', data={'output': 'A', 'input': 2})
    assert 'Command failed (504)' in result.text
    assert 'Routing is uncertain' in result.text
    assert '&lt;script&gt;' in result.text
    assert matrix.status_reads == 1
    assert cache.remembered().requested == {}


def test_unknown_legacy_direction_is_marked_uncertain(setup):
    client, matrix, _, _ = setup
    client.get('/')
    assert client.post('/set-output-input', json={'output': 'B', 'input': 'U'}).status_code == 200
    assert 'Routing is uncertain' in client.get('/ui/state').text
    assert matrix.status_reads == 1
