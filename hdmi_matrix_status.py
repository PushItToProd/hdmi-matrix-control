"""
Parser for the HDMI matrix STA (system status) command output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VideoMode(Enum):
    """Output A display mode."""
    SINGLE   = "single"    # Single input full-screen
    TWO_X_TWO = "2x2"     # Four inputs in a 2x2 grid
    TWO_PLR  = "2PLR"      # Two inputs side by side (left/right)
    TWO_PUD  = "2PUD"      # Two inputs stacked (up/down)
    ONE_BIG_THREE_SMALL = "1B3S"  # One large + three small
    PIP      = "PIP"       # Picture-in-picture


class AudioMode(Enum):
    CH_2_1 = "2.1CH"
    CH_5_1 = "5.1CH"


# ---------------------------------------------------------------------------
# Video mode detail dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SingleInputMode:
    """Output A or B showing a single full-screen input."""
    input: int  # 1-4


@dataclass
class TwoByTwoMode:
    """Four inputs arranged in a 2x2 grid."""
    combination: int  # 1-4


@dataclass
class SideBySideMode:
    """Two inputs shown left and right."""
    left: int   # 1-4
    right: int  # 1-4


@dataclass
class TopBottomMode:
    """Two inputs shown top and bottom."""
    top: int    # 1-4
    bottom: int # 1-4


@dataclass
class OneBigThreeSmallMode:
    """One large picture with three smaller ones."""
    combination: int  # 1-4


@dataclass
class PIPMode:
    """Picture-in-picture."""
    main: int   # 1-4
    small: int  # 1-4


# Union type alias for output A mode detail
type OutputADetail = (
    SingleInputMode
    | TwoByTwoMode
    | SideBySideMode
    | TopBottomMode
    | OneBigThreeSmallMode
    | PIPMode
)


# ---------------------------------------------------------------------------
# Per-output status
# ---------------------------------------------------------------------------

@dataclass
class OutputAStatus:
    mode: VideoMode
    detail: OutputADetail
    resolution: str  # e.g. "1080p", "4K30"


@dataclass
class OutputBStatus:
    input: int        # 1-4; 0 when the device reports no single input: copying A
                      # in a multi-picture mode, or "no display" because the
                      # selected input has no signal
    resolution: str   # e.g. "1920x1080p"; "N/A" when there is no display
    copy_outa: bool   # True when COPY OUTA MODE = ON


# ---------------------------------------------------------------------------
# Top-level status dataclass
# ---------------------------------------------------------------------------

@dataclass
class HDMIMatrixStatus:
    # Device info
    model: str           # e.g. "4PET0402QMS"
    device_name: str     # e.g. "4PET0402QMS_0001"
    firmware_version: str

    # System state
    power_on: bool
    front_panel_buttons_enabled: bool

    # RS-232 config
    baud_rate: int       # e.g. 57600
    data_bits: int       # e.g. 8
    parity: str          # e.g. "None"
    stop_bits: int       # e.g. 1

    # Video inputs (index 0 = input 1)
    input_links: list[bool]  # len 4; True = LINK ON

    # Video output
    video_output_on: bool
    dbg_on: bool

    # Per-output status
    output_a: OutputAStatus
    output_b: OutputBStatus

    # Audio
    audio_output_enabled: bool
    audio_mode: AudioMode
    audio_input_channel: int  # 1-4


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _strip_line(raw: str) -> str:
    """Remove the leading/trailing '--' framing and surrounding whitespace."""
    return raw.strip().lstrip('-').rstrip('-').strip()


def _parse_output_a(mode_str: str, resolution: str) -> OutputAStatus:
    """
    Parse the 'Output A Video Mode' field into an OutputAStatus.

    Observed formats:
        Input4
        2x2 1
        2PLR 1 4
        2PUD 1 4
        1B3S 1
        PIP 1 4
    """
    parts = mode_str.split()

    if parts[0].startswith('Input'):
        inp = int(parts[0][len('Input'):])
        return OutputAStatus(VideoMode.SINGLE, SingleInputMode(inp), resolution)

    tag = parts[0]

    if tag == '2x2':
        return OutputAStatus(VideoMode.TWO_X_TWO, TwoByTwoMode(int(parts[1])), resolution)

    if tag == '2PLR':
        return OutputAStatus(VideoMode.TWO_PLR, SideBySideMode(int(parts[1]), int(parts[2])), resolution)

    if tag == '2PUD':
        return OutputAStatus(VideoMode.TWO_PUD, TopBottomMode(int(parts[1]), int(parts[2])), resolution)

    if tag == '1B3S':
        return OutputAStatus(VideoMode.ONE_BIG_THREE_SMALL, OneBigThreeSmallMode(int(parts[1])), resolution)

    if tag == 'PIP':
        return OutputAStatus(VideoMode.PIP, PIPMode(int(parts[1]), int(parts[2])), resolution)

    raise ValueError(f"Unrecognised Output A video mode: {mode_str!r}")


def parse_status(raw: str) -> HDMIMatrixStatus:
    """
    Parse the full STA command response into an HDMIMatrixStatus dataclass.

    Args:
        raw: The complete response string from the STA command, including
             the banner lines.

    Returns:
        A populated HDMIMatrixStatus instance.

    Raises:
        ValueError: If a required field cannot be found or parsed.
    """
    # Extract only the content lines (those starting with '--')
    content_lines = [
        _strip_line(line)
        for line in raw.splitlines()
        if re.match(r'\s*--', line)
    ]
    # Drop pure separator lines (empty after stripping)
    content_lines = [l for l in content_lines if l]

    # Join into a single searchable string for regex use
    body = '\n'.join(content_lines)

    def _find(pattern: str, flags=0) -> re.Match:
        m = re.search(pattern, body, flags)
        if not m:
            raise ValueError(f"Could not find pattern {pattern!r} in STA output")
        return m

    # -- Device identity ---------------------------------------------------
    # "4PET0402QMS,   Device Name: 4PET0402QMS_0001"
    dev_m = _find(r'^(\S+),\s+Device Name:\s+(\S+)', re.MULTILINE)
    model       = dev_m.group(1)
    device_name = dev_m.group(2)

    fw_m = _find(r'F/W Version:\s+(\S+)')
    firmware_version = fw_m.group(1)

    # -- System state ------------------------------------------------------
    power_on = _find(r'Power\s*:\s*(\w+)').group(1).upper() == 'ON'

    fpb = _find(r'Front Panel Button\s*:\s*(\w+)').group(1).upper()
    front_panel_buttons_enabled = fpb == 'ENABLED'

    # -- RS-232 config -----------------------------------------------------
    # "RS232 : Baud Rate=57600bps, Data=8bit, Parity=None, Stop=1bit"
    rs_m = _find(
        r'RS232\s*:.*?Baud Rate=(\d+)bps.*?Data=(\d+)bit.*?Parity=(\w+).*?Stop=(\d+)bit'
    )
    baud_rate = int(rs_m.group(1))
    data_bits = int(rs_m.group(2))
    parity    = rs_m.group(3)
    stop_bits = int(rs_m.group(4))

    # -- Video inputs ------------------------------------------------------
    # "Video Input 01 :  LINK = ON"
    input_links: list[bool] = []
    for i in range(1, 5):
        link_m = _find(rf'Video Input {i:02d}\s*:.*?LINK\s*=\s*(\w+)')
        input_links.append(link_m.group(1).upper() == 'ON')

    # -- Video output state ------------------------------------------------
    # "Video Output: Output  = ON , DBG = ON"
    vo_m = _find(r'Video Output:.*?Output\s*=\s*(\w+).*?DBG\s*=\s*(\w+)')
    video_output_on = vo_m.group(1).upper() == 'ON'
    dbg_on          = vo_m.group(2).upper() == 'ON'

    # -- Output A ----------------------------------------------------------
    # "Output A Video Mode: PIP 1 4 , RES = 1080p"
    # The resolution is informational only, so accept any text (including
    # none) rather than fail the whole status over an unexpected value.
    oa_m = _find(r'Output A Video Mode:\s*(.+?)\s*,\s*RES\s*=[ \t]*(.*?)[ \t]*$', re.MULTILINE)
    output_a = _parse_output_a(oa_m.group(1).strip(), oa_m.group(2))

    # -- Output B ----------------------------------------------------------
    # "Output B Video Mode: Input1 , RES = 1920x1080p, COPY OUTA MODE = OFF"
    # When B copies A, the device reports A's layout here instead, e.g.
    # "Output B Video Mode: PIP 1 4 , RES = 1080p , COPY OUTA MODE = ON".
    # When B's selected input has no signal the device rewrites the whole
    # line, dropping the input number and renaming the copy field:
    # "Output B Video Mode: no display, RES = N/A,  Copy OutputA Mode = OFF".
    ob_m = _find(
        r'Output B Video Mode:\s*(.+?)\s*,\s*RES\s*=\s*(.*?)\s*,\s*Copy\s+Out(?:A|putA)\s+Mode\s*=\s*(\w+)',
        re.IGNORECASE,
    )
    ob_mode = ob_m.group(1).strip()
    ob_input = int(ob_mode[len('Input'):]) if ob_mode.startswith('Input') else 0
    output_b = OutputBStatus(
        input      = ob_input,
        resolution = ob_m.group(2),
        copy_outa  = ob_m.group(3).upper() == 'ON',
    )

    # -- Audio -------------------------------------------------------------
    audio_enabled = _find(r'Audio Output\s*:\s*(\w+)').group(1).upper() == 'ENABLED'

    am_raw = _find(r'Audio Mode\s*:\s*(\S+)').group(1).upper()
    try:
        audio_mode = AudioMode(am_raw)
    except ValueError:
        raise ValueError(f"Unrecognised audio mode: {am_raw!r}")

    audio_ch = int(_find(r'Audio Input Channel\s*:\s*Input\s*(\d+)').group(1))

    return HDMIMatrixStatus(
        model                        = model,
        device_name                  = device_name,
        firmware_version             = firmware_version,
        power_on                     = power_on,
        front_panel_buttons_enabled  = front_panel_buttons_enabled,
        baud_rate                    = baud_rate,
        data_bits                    = data_bits,
        parity                       = parity,
        stop_bits                    = stop_bits,
        input_links                  = input_links,
        video_output_on              = video_output_on,
        dbg_on                       = dbg_on,
        output_a                     = output_a,
        output_b                     = output_b,
        audio_output_enabled         = audio_enabled,
        audio_mode                   = audio_mode,
        audio_input_channel          = audio_ch,
    )
