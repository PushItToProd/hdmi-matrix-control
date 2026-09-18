"""
HDMI Matrix RS232/USB Controller
Encapsulates the RS232 protocol for a 4PET0402QMS 4x2 HDMI matrix switch.

Protocol notes:
- Commands are sent as ASCII strings terminated with a carriage return (\\r)
- Responses are terminated with \\r\\n
- Commands are not case-sensitive
"""

import logging
import threading
import time
from typing import overload, Literal

import serial

from hdmi_matrix_status import parse_status, HDMIMatrixStatus

logger = logging.getLogger(__name__)


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

    # Every reply ends with the command-info line "<s>CMD</s><user>...</user>"
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
        read_delay: float = 0.1,
    ):
        """
        Open a serial connection to the HDMI matrix.

        Args:
            port:        Serial port, e.g. '/dev/ttyACM0' or 'COM3'.
            baudrate:    Must match the device's configured baud rate (default 57600).
            timeout:     Read timeout in seconds; bounds a read whose reply never
                         carries the terminator (H, SPOBCOPYOUTAOFF).
            read_delay:  Seconds a quick (fire-and-forget) command keeps the port
                         after writing, so the device's reply has fully arrived
                         and the next command's input-buffer reset discards it.
        """
        self._read_delay = read_delay
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

    @overload
    def _send(self, command: str) -> str: ...

    @overload
    def _send(self, command: str, quick: Literal[False]) -> str: ...

    @overload
    def _send(self, command: str, quick: Literal[True]) -> None: ...

    def _send(self, command: str, quick=False) -> str | None:
        """
        Send a command string (carriage return appended automatically) and
        return the decoded response.

        Thread-safe: Uses a lock to ensure only one command is sent at a time,
        preventing concurrent writes to the serial port. The lock is held for
        as short a time as possible because every caller shares one serial
        port: a status poll that lingers here delays every switch command
        queued behind it.
        """
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(f'{command}\r'.encode('ascii'))

            if quick:
                time.sleep(self._read_delay)
                return None

            response = self._serial.read_until(self.RESPONSE_TERMINATOR)
            return response.decode('ascii', errors='replace')

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

    def set_output_input(self, output: str, inp, quick=False) -> str | None:
        """
        Route a video input to a single output.

        Args:
            output: 'A' or 'B'.
            inp:    Input number 1-4, or 'U'/'D' to step up/down.
        """
        output = self._validate_output(output)
        inp = self._validate_input(inp)
        return self._send(f'SPO{output}SI{inp}', quick=quick)

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
