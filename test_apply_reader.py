"""Replay measured byte bursts; synthetic cases cover unobserved failures."""
import base64
import json
from pathlib import Path

import pytest

import hdmi_matrix
from hdmi_matrix import CommandRejected, CommandTimeout
from test_hdmi_matrix import matrix_with

CAPTURES = Path(__file__).parent / 'test_fixtures/captures/2026-09-19'


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ReplaySerial:
    """Deliver recorded bursts on a virtual clock, splitting at read_until.

    Burst timestamps are first-byte times, not per-byte timing measurements.
    This tests framing and ordering without pretending to measure hardware.
    """
    def __init__(self, clock, bursts, timeout=1.0):
        self.clock = clock
        self.bursts = list(bursts)
        self.buffer = b''
        self.timeout = timeout
        self.writes = []
        self.timeouts = []
        self.resets = 0

    def reset_input_buffer(self):
        self.resets += 1
        self.buffer = b''

    def write(self, data):
        self.writes.append((self.clock.now, data))

    @property
    def in_waiting(self):
        while self.bursts and self.bursts[0][0] <= self.clock.now:
            _, data = self.bursts.pop(0)
            self.buffer += data
        return len(self.buffer)

    def read_until(self, terminator, size=None):
        self.timeouts.append(self.timeout)
        deadline = self.clock.now + self.timeout
        while terminator not in self.buffer and (size is None or len(self.buffer) < size):
            if not self.bursts or self.bursts[0][0] > deadline:
                self.clock.now = deadline
                result, self.buffer = self.buffer, b''
                return result
            at, data = self.bursts.pop(0)
            self.clock.now = max(self.clock.now, at)
            self.buffer += data
        end = (self.buffer.index(terminator) + len(terminator)
               if terminator in self.buffer else len(self.buffer))
        if size is not None:
            end = min(end, size)
        result, self.buffer = self.buffer[:end], self.buffer[end:]
        return result


def setup(monkeypatch, bursts, timeout=1.0):
    clock = Clock()
    monkeypatch.setattr(hdmi_matrix, 'time', clock)
    serial = ReplaySerial(clock, bursts, timeout)
    return matrix_with(serial), serial, clock


@pytest.mark.parametrize('name', [
    '01-swap-100ms', '04-mode-and-b', '05-gap-0.10',
])
def test_recorded_replies_are_fully_drained(monkeypatch, name):
    capture = json.loads((CAPTURES / (name + '.json')).read_text())
    bursts = [(e['at_ms'] / 1000, base64.b64decode(e['base64']))
              for e in capture['events'] if e['kind'] == 'read']
    matrix, serial, clock = setup(monkeypatch, bursts)
    commands = capture['request']['commands']
    actual = matrix.send_commands(commands)
    assert actual.encode() == (CAPTURES / (name + '.bin')).read_bytes()
    assert serial.writes == [(0.0, (commands[0] + '\r').encode()),
                             (0.1, (commands[1] + '\r').encode())]
    assert not serial.buffer and not serial.bursts
    assert serial.timeout == 1.0
    assert clock.now < 1.0


def reply(command):
    return f'<s>{command}</s><user>ack</user>\r'.encode()


def test_unexpected_echo_does_not_finish_status_read(monkeypatch):
    matrix, serial, clock = setup(monkeypatch, [(.01, reply('SPOBSI01')), (.35, reply('STA'))])
    response = matrix._send('STA')
    assert response.endswith(reply('STA').decode())
    assert clock.now == .35
    assert serial.timeouts[1] < serial.timeouts[0]


def test_missing_reply_costs_one_overall_deadline(monkeypatch):
    matrix, serial, clock = setup(monkeypatch, [(.3, reply('SPOASI01'))])
    with pytest.raises(CommandTimeout, match='SPOBSI04'):
        matrix.send_commands(['SPOASI01', 'SPOBSI04'])
    assert clock.now == pytest.approx(1.1)
    assert serial.timeout == 1.0
    assert len(serial.writes) == 2  # no replay after an uncertain outcome


def test_rejection_drains_other_reply_then_fails(monkeypatch):
    matrix, serial, clock = setup(monkeypatch, [(.11, reply('?')), (.4, reply('SPOBSI04'))])
    with pytest.raises(CommandRejected):
        matrix.send_commands(['BOGUS', 'SPOBSI04'])
    assert clock.now == .4
    assert not serial.buffer and not serial.bursts
    assert serial.timeout == 1.0


def test_single_rejection_is_terminal(monkeypatch):
    matrix, serial, clock = setup(monkeypatch, [(.01, reply('?'))])
    with pytest.raises(CommandRejected):
        matrix._send('BOGUS')
    assert clock.now == .01


def test_echo_without_complete_trailer_is_not_success(monkeypatch):
    matrix, serial, clock = setup(monkeypatch, [(.01, b'<s>STA</s><user>partial')])
    with pytest.raises(CommandTimeout):
        matrix._send('STA')
    assert clock.now == 1.0


def test_accumulates_partial_reads_and_out_of_order_echoes(monkeypatch):
    matrix, serial, clock = setup(monkeypatch, [])
    chunks = iter([reply('SPOBSI04'), b'<s>SPOA', b'SI01</s><user>ack</user>\r'])
    serial.read_until = lambda _, **kwargs: next(chunks)
    assert matrix.send_commands(['SPOASI01', 'SPOBSI04']) == (reply('SPOBSI04') + reply('SPOASI01')).decode()


def test_early_ack_cannot_advance_next_request_before_gap(monkeypatch):
    matrix, serial, clock = setup(monkeypatch, [(.005, reply('SPOASI01')), (.12, reply('STA'))])
    matrix.set_output_input('A', 1)
    matrix._send('STA')
    assert serial.writes == [(0, b'SPOASI01\r'), (.1, b'STA\r')]


def test_timeout_is_restored_on_serial_failure(monkeypatch):
    matrix, serial, _ = setup(monkeypatch, [])
    def fail(_, **kwargs):
        raise OSError('port lost')
    serial.read_until = fail
    with pytest.raises(OSError):
        matrix._send('STA')
    assert serial.timeout == 1.0


@pytest.mark.parametrize('command', ['H', 'SPOBCOPYOUTAON', 'SPOBCOPYOUTAOFF'])
def test_documented_unframed_commands_still_drain_the_read_window(monkeypatch, command):
    matrix, serial, clock = setup(monkeypatch, [(.02, b'help or silence')])
    assert matrix._send(command) == 'help or silence'
    assert clock.now == 1.0


def test_batch_owns_port_through_writes_and_reads(monkeypatch):
    matrix, serial, _ = setup(monkeypatch, [(.01, reply('SPOASI01')), (.3, reply('SPOBSI04'))])
    held = []
    original_write, original_read = serial.write, serial.read_until
    def probe():
        acquired = matrix._lock.acquire(blocking=False)
        held.append(not acquired)
        if acquired:
            matrix._lock.release()
    def write(data):
        probe()
        original_write(data)
    def read(terminator, **kwargs):
        probe()
        return original_read(terminator, **kwargs)
    serial.write, serial.read_until = write, read
    matrix.send_commands(['SPOASI01', 'SPOBSI04'])
    assert held and all(held)
    assert matrix._lock.acquire(blocking=False)
    matrix._lock.release()


def test_pyserial_late_partial_reply_cannot_extend_overall_deadline(monkeypatch):
    from serial.serialutil import SerialBase
    matrix, serial, clock = setup(monkeypatch, [(.99, b'<s>STA</s><user>unfinished')])
    # Use pyserial's actual implementation, whose timeout is per read(1),
    # against our scheduled transport. The next byte never arrives.
    serial._timeout = 1.0
    def read(size):
        return ReplaySerial.read_until(serial, b'\x00', size=size)
    serial.read = read
    serial.read_until = SerialBase.read_until.__get__(serial)
    with pytest.raises(CommandTimeout):
        matrix._send('STA')
    assert clock.now == pytest.approx(1.0)
    assert serial.timeout == 1.0
