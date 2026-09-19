"""
HDMI Matrix RS232/USB Controller
Encapsulates the RS232 protocol for a 4PET0402QMS 4x2 HDMI matrix switch.

Protocol notes:
- Commands are sent as ASCII strings terminated with a carriage return (\\r)
- Most replies end with </user> followed by a bare carriage return
- Commands are not case-sensitive
"""

import logging
import math
import re
import threading
import time
from collections import Counter

import serial

from hdmi_matrix_status import parse_status, HDMIMatrixStatus

logger = logging.getLogger(__name__)


class CommandTimeout(TimeoutError):
    """Some commands may have applied, but their complete replies are missing."""


class CommandRejected(RuntimeError):
    """The device returned its unknown-command marker."""


class HDMIMatrix:
    """
    Controls a 4x2 HDMI matrix switch over USB CDC (RS232).

    Example usage:
        matrix = HDMIMatrix('/dev/ttyACM0')
        matrix.get_status()
        matrix.set_output_input('A', 1)
        matrix.close()

    Or as a context manager:
        with HDMIMatrix('/dev/ttyACM0') as matrix:
            matrix.power_on()
            matrix.set_output_input('A', 2)
    """

    # Valid values for parameters used across multiple methods
    VALID_OUTPUTS = ('A', 'B')
    VALID_INPUTS = (1, 2, 3, 4)
    VALID_INPUT_DIRS = ('U', 'D')  # Up/Down step through inputs
    VALID_BAUD_CODES = {0: 57600, 1: 38400, 2: 19200, 3: 9600, 4: 4800}
    VALID_PIP_CORNERS = ('RD', 'LD', 'LU', 'RU')  # for reference only

    # Most replies end with the command-info line "<s>CMD</s><user>...</user>"
    # followed by a bare carriage return and no line feed (see protocol.md and
    # test_fixtures/responses). Reading to this marker ends the read the moment
    # the reply is complete; waiting for CRLF instead would block for the full
    # serial timeout on every command, which made STA take over a second.
    RESPONSE_TERMINATOR = b'</user>\r'

    def __init__(
        self,
        port: str = '/dev/ttyACM0',
        baudrate: int = 57600,
        timeout: float = 1.0,
        command_gap: float = 0.1,
    ):
        """
        Open a serial connection to the HDMI matrix.

        Args:
            port:        Serial port, e.g. '/dev/ttyACM0' or 'COM3'.
            baudrate:    Must match the device's configured baud rate (default 57600).
            timeout:     Read timeout in seconds; bounds a read whose reply never
                         carries the terminator (H, SPOBCOPYOUTAOFF).
            command_gap: Minimum seconds between writes, including commands in
                         separate requests and a subsequent status query.
        """
        if not math.isfinite(command_gap) or command_gap < 0:
            raise ValueError('command_gap must be finite and non-negative')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        self._command_gap = command_gap
        self._next_write_at = 0.0
        self._serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
        )
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        """Close the serial connection."""
        if self._serial.is_open:
            self._serial.close()

    # ------------------------------------------------------------------
    # Low-level communication
    # ------------------------------------------------------------------

    def _send(self, command: str) -> str:
        # These commands are documented to omit their trailer (or all output).
        # They still drain the port for the entire bounded read window.
        return self.send_commands([command], allow_unframed=command.upper() in
                                  ('H', 'SPOBCOPYOUTAON', 'SPOBCOPYOUTAOFF'))

    def _pace(self):
        remaining = self._next_write_at - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def send_commands(self, commands: list[str], *, allow_unframed: bool = False) -> str:
        """Write a batch under one lock, then drain every complete command echo.

        The timeout is one overall read budget after the last write, not a
        timeout per reply. Echoes only acknowledge receipt: callers must still
        verify routing. Never retry a partially applied batch automatically.
        """
        if not commands or any(not c or not c.isascii() or not c.isprintable() for c in commands):
            raise ValueError('commands must be nonempty printable ASCII')
        expected = Counter(c.upper().encode('ascii') for c in commands)
        with self._lock:
            previous_timeout = self._serial.timeout
            try:
                self._pace()
                self._serial.reset_input_buffer()
                for command in commands:
                    self._pace()
                    self._serial.write(f'{command}\r'.encode('ascii'))
                    self._next_write_at = time.monotonic() + self._command_gap
                deadline = time.monotonic() + previous_timeout
                response = bytearray()
                rejected = 0
                missing = expected.copy()
                while (remaining := deadline - time.monotonic()) > 0:
                    self._serial.timeout = remaining
                    # pyserial's read_until does repeated read(1) calls with
                    # the same timeout. A late partial reply could otherwise
                    # start another full wait inside that call. Read only the
                    # buffered bytes, or one blocking byte, then recompute the
                    # remaining budget before waiting again.
                    response.extend(self._serial.read_until(
                        self.RESPONSE_TERMINATOR, size=max(1, self._serial.in_waiting)))
                    # Require the whole trailer, not just </s>: leaving its
                    # tail unread would corrupt the following transaction.
                    echoes = Counter(re.findall(rb'<s>([^<]+)</s><user>.*?</user>\r',
                                                response, re.DOTALL))
                    missing = expected - echoes
                    rejected = echoes[b'?']
                    if sum(missing.values()) <= rejected:
                        break
                decoded = response.decode('ascii', errors='replace')
                if rejected:
                    raise CommandRejected(f'device rejected batch {commands!r}: {decoded}')
                if missing and not allow_unframed:
                    names = [c.decode('ascii') for c in missing.elements()]
                    raise CommandTimeout(f'missing replies for {names!r}: {decoded}')
                return decoded
            finally:
                self._serial.timeout = previous_timeout

    # ------------------------------------------------------------------
    # Raw capture
    # ------------------------------------------------------------------

    def send_raw(
        self,
        commands: list[str],
        gap: float = 0.1,
        read_seconds: float = 1.5,
        settle: float = 0.02,
        reset_input: bool = True,
    ) -> list[dict]:
        """
        Write commands back to back and record every byte the device sends,
        with arrival times. Used to learn what the device actually does with
        commands that arrive while it is still working on the previous one;
        the framing the rest of this class relies on is derived from captures
        taken this way.

        Unlike `_send`, this makes no assumption about where a reply ends. It
        reads for a fixed window and reports what arrived, so a reply that is
        late, interleaved with another, or absent shows up as such instead of
        being mistaken for the next command's reply.

        Args:
            commands:     Commands to write, without the trailing carriage
                          return. Written in order.
            gap:          Seconds between writes. The device drops a command
                          that arrives too soon after the previous one.
            read_seconds: How long to keep reading after the last write.
            settle:       Bytes still arriving within this many seconds join
                          the preceding chunk, so one reply is one event
                          rather than hundreds of single-byte ones.
            reset_input:  Discard buffered input before the first write.

        Returns:
            Events in order, each `{"at_ms", "kind", ...}` where `at_ms` is
            milliseconds since the first write. A "write" event carries
            `command`; a "read" event carries the `data` bytes.
        """
        events: list[dict] = []
        with self._lock:
            previous_timeout = self._serial.timeout
            try:
                if reset_input:
                    self._serial.reset_input_buffer()
                self._pace()
                start = time.monotonic()
                for index, command in enumerate(commands):
                    if index:
                        self._capture_until(start, time.monotonic() + gap, settle, events)
                    self._serial.write(f'{command}\r'.encode('ascii'))
                    self._next_write_at = time.monotonic() + self._command_gap
                    events.append({
                        'at_ms': round((time.monotonic() - start) * 1000, 1),
                        'kind': 'write',
                        'command': command,
                    })
                self._capture_until(start, time.monotonic() + read_seconds, settle, events)
            finally:
                self._serial.timeout = previous_timeout
        return events

    def _capture_until(self, start: float, deadline: float, settle: float, events: list[dict]):
        """Read until `deadline`, appending one event per burst of bytes."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._serial.timeout = remaining
            chunk = self._serial.read(1)
            if not chunk:
                continue  # nothing arrived before the deadline
            at_ms = round((time.monotonic() - start) * 1000, 1)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._serial.timeout = min(settle, remaining)
                more = self._serial.read(max(1, self._serial.in_waiting))
                if not more:
                    break
                chunk += more
            events.append({'at_ms': at_ms, 'kind': 'read', 'data': chunk})

    @classmethod
    def _validate_output(cls, output: str):
        """Accept output letter 'A' or 'B', case-insensitive."""
        output = output.upper()
        if output not in cls.VALID_OUTPUTS:
            raise ValueError(f"Output must be one of {cls.VALID_OUTPUTS}, got {output!r}")
        return output

    @classmethod
    def _validate_input(cls, inp):
        """Accept int 1-4 or direction string 'U'/'D'."""
        if isinstance(inp, str):
            inp = inp.upper()
            if inp in cls.VALID_INPUT_DIRS:
                return inp
        if inp in cls.VALID_INPUTS:
            return f'{int(inp):02d}'
        raise ValueError(
            f"Input must be 1-4 or one of {cls.VALID_INPUT_DIRS}, got {inp!r}"
        )

    @classmethod
    def _validate_picture_input(cls, inp: int, name: str = 'input') -> int:
        if inp not in cls.VALID_INPUTS:
            raise ValueError(f"{name} must be one of {cls.VALID_INPUTS}, got {inp!r}")
        return inp

    # ------------------------------------------------------------------
    # General commands
    # ------------------------------------------------------------------

    def help(self) -> str:
        """Request the help text from the device."""
        return self._send('H')

    def power_off(self) -> str:
        """Power the device off."""
        return self._send('PF')

    def power_on(self) -> str:
        """Power the device on."""
        return self._send('PN')

    def _get_status_str(self) -> str:
        """Request the raw status string from the device."""
        return self._send('STA')

    def get_status(self) -> HDMIMatrixStatus:
        """Return the global system status as a dataclass."""
        raw_status = self._get_status_str()
        try:
            return parse_status(raw_status)
        except ValueError:
            # The device rewords status lines in states nobody has recorded
            # yet; the raw text is the only way to see what it actually said.
            logger.error("Unparseable STA output:\n%s", raw_status)
            raise

    # ------------------------------------------------------------------
    # Video output setup
    # ------------------------------------------------------------------

    def set_output_input(self, output: str, inp) -> str:
        """
        Route a video input to a single output.

        Args:
            output: 'A' or 'B'.
            inp:    Input number 1-4, or 'U'/'D' to step up/down.
        """
        output = self._validate_output(output)
        inp = self._validate_input(inp)
        return self._send(f'SPO{output}SI{inp}')

    def set_all_outputs_input(self, inp) -> str:
        """
        Route a video input to both outputs A and B simultaneously.

        Args:
            inp: Input number 1-4, or 'U'/'D' to step up/down.
        """
        inp = self._validate_input(inp)
        return self._send(f'SPOSI{inp}')

    def set_output_power(self, on: bool) -> str:
        """Enable or disable the video output."""
        return self._send(f'SPO{"ON" if on else "OFF"}')

    # -- Output A multi-picture modes ------------------------------------

    def set_output_a_2x2(self, combination: int) -> str:
        """
        Set output A to four-input 2x2 picture mode.

        Args:
            combination: Layout combination 1-4.
        """
        self._validate_picture_input(combination, 'combination')
        return self._send(f'SPOA2x2{combination}')

    def set_output_a_side_by_side(self, left: int, right: int) -> str:
        """
        Set output A to two-picture left/right mode.

        Args:
            left:  Input number for the left picture (1-4).
            right: Input number for the right picture (1-4).
        """
        self._validate_picture_input(left, 'left')
        self._validate_picture_input(right, 'right')
        return self._send(f'SPOA2PLR{left}{right}')

    def set_output_a_top_bottom(self, top: int, bottom: int) -> str:
        """
        Set output A to two-picture up/down mode.

        Args:
            top:    Input number for the upper picture (1-4).
            bottom: Input number for the lower picture (1-4).
        """
        self._validate_picture_input(top, 'top')
        self._validate_picture_input(bottom, 'bottom')
        return self._send(f'SPOA2PUD{top}{bottom}')

    def set_output_a_1big3small(self, combination: int) -> str:
        """
        Set output A to one-big-three-small (1B3S) picture mode.

        Args:
            combination: Layout combination 1-4.
        """
        self._validate_picture_input(combination, 'combination')
        return self._send(f'SPOA1B3S{combination}')

    def set_output_a_pip(self, main: int, small: int) -> str:
        """
        Set output A to picture-in-picture (PIP) mode.

        Args:
            main:  Input number for the main (large) picture (1-4).
            small: Input number for the small overlay picture (1-4).
        """
        self._validate_picture_input(main, 'main')
        self._validate_picture_input(small, 'small')
        return self._send(f'SPOAPIP{main}{small}')

    def rotate_pip_position(self) -> str:
        """
        Cycle the PIP overlay corner:
        bottom-right -> bottom-left -> top-left -> top-right.
        """
        return self._send('SPOAPIPROTATE')

    def rotate_output_a_resolution(self) -> str:
        """
        Cycle output A resolution: 4K30 -> 2560x1600p -> 1080p.
        """
        return self._send('SPOASCALERROTATE')

    def rotate_output_a_aspect_ratio(self) -> str:
        """
        Toggle output A aspect ratio between full screen and original ratio.
        """
        return self._send('SPOARATIOROTATE')

    # -- Output B --------------------------------------------------------

    def set_output_b_copy_a(self, enabled: bool) -> str:
        """
        Enable or disable output B mirroring output A.

        Args:
            enabled: True to mirror output A, False for independent operation.
        """
        return self._send(f'SPOBCOPYOUTA{"ON" if enabled else "OFF"}')

    # ------------------------------------------------------------------
    # Audio output setup
    # ------------------------------------------------------------------

    def set_audio_output(self, enabled: bool) -> str:
        """Enable or disable external optical and analog audio output."""
        return self._send(f'SPOA{"E" if enabled else "D"}')

    def set_audio_mode(self, mode: str) -> str:
        """
        Set the default audio channel mode.

        Args:
            mode: '2.1' or '5.1'.
        """
        mode = mode.strip()
        if mode not in ('2.1', '5.1'):
            raise ValueError(f"Audio mode must be '2.1' or '5.1', got {mode!r}")
        return self._send(f'SPOAM{mode}')

    def set_output_a_audio_input(self, inp: int) -> str:
        """
        Select the audio input channel used for output A in multi-picture modes.

        Args:
            inp: Input number 1-4.
        """
        self._validate_picture_input(inp, 'inp')
        return self._send(f'SPOAA{inp}')

    # ------------------------------------------------------------------
    # System control
    # ------------------------------------------------------------------

    def show_osd(self) -> str:
        """Display OSD information overlay (auto-dismisses after 5 seconds)."""
        return self._send('SHOWOSD')

    def set_front_panel_buttons(self, enabled: bool) -> str:
        """Enable or disable the physical front panel buttons."""
        return self._send(f'SPCFB{"E" if enabled else "D"}')

    def set_baud_rate(self, code: int) -> str:
        """
        Set the RS-232 baud rate.

        Args:
            code: Integer 0-4 mapping to:
                  0 = 57600, 1 = 38400, 2 = 19200, 3 = 9600, 4 = 4800.

        Note:
            After calling this you must re-open the serial port at the new
            baud rate, or subsequent commands will fail.
        """
        if code not in self.VALID_BAUD_CODES:
            raise ValueError(
                f"Baud code must be one of {list(self.VALID_BAUD_CODES.keys())}, "
                f"got {code!r}. Mapping: {self.VALID_BAUD_CODES}"
            )
        return self._send(f'SPCRSB{code}')

    def factory_reset(self) -> str:
        """Reset all settings to factory defaults."""
        return self._send('SPCDF')
