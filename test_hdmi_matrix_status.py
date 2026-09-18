"""
Parser tests over the STA replies recorded in test_fixtures/responses.

Run without the project environment:
    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest \
        pytest -p no:cacheprovider test_hdmi_matrix_status.py
"""

from pathlib import Path

import pytest

from hdmi_matrix_status import (
    OneBigThreeSmallMode,
    PIPMode,
    SideBySideMode,
    SingleInputMode,
    TopBottomMode,
    TwoByTwoMode,
    VideoMode,
    parse_status,
)

FIXTURES = Path(__file__).parent / "test_fixtures" / "responses"
ALL_STATUSES = sorted(p.name.removesuffix("_status.bin") for p in FIXTURES.glob("*_status.bin"))


def load(name: str) -> str:
    return (FIXTURES / f"{name}_status.bin").read_bytes().decode()


@pytest.mark.parametrize("name", ALL_STATUSES)
def test_every_recorded_status_parses(name):
    status = parse_status(load(name))
    assert status.model == "4PET0402QMS"
    assert status.power_on
    assert len(status.input_links) == 4
    assert status.baud_rate == 57600


@pytest.mark.parametrize(
    "name, mode, detail",
    [
        ("00_spobsi01", VideoMode.SINGLE, SingleInputMode(4)),
        ("01_spoa2plr14", VideoMode.TWO_PLR, SideBySideMode(1, 4)),
        ("02_spoa2pud14", VideoMode.TWO_PUD, TopBottomMode(1, 4)),
        ("03_spoa2x21", VideoMode.TWO_X_TWO, TwoByTwoMode(1)),
        ("04_spoa1b3s1", VideoMode.ONE_BIG_THREE_SMALL, OneBigThreeSmallMode(1)),
        ("05_spoapip14", VideoMode.PIP, PIPMode(1, 4)),
        ("08_spoasi04", VideoMode.SINGLE, SingleInputMode(4)),
    ],
)
def test_output_a_modes(name, mode, detail):
    a = parse_status(load(name)).output_a
    assert a.mode == mode
    assert a.detail == detail
    assert a.resolution == "1080p"


def test_output_b_copying_a_reports_a_layout_and_no_input():
    b = parse_status(load("06_spob copy outa on")).output_b
    assert b.copy_outa
    assert b.input == 0
    assert b.resolution == "1080p"


def test_output_b_independent():
    b = parse_status(load("07_spob copy outa off")).output_b
    assert not b.copy_outa
    assert b.input == 1
    assert b.resolution == "1920x1080p"


def test_output_b_no_display_when_input_has_no_signal():
    # The device rewrites B's line entirely in this state: no input number,
    # "N/A" for the resolution, and a differently spelled copy field.
    status = parse_status(load("09_spob no display"))
    assert status.input_links == [False, False, False, True]
    assert status.output_a.detail == SingleInputMode(1)
    b = status.output_b
    assert not b.copy_outa
    assert b.input == 0
    assert b.resolution == "N/A"


_A_LINE = "-- Output A Video Mode: Input4 , RES = 1080p                           --"
_B_LINE = "-- Output B Video Mode: Input1 , RES = 1920x1080p, COPY OUTA MODE = OFF--"


@pytest.mark.parametrize("res", ["", "No Signal", "N/A"])
def test_unexpected_resolution_text_does_not_fail_the_status(res):
    raw = load("00_spobsi01")
    raw = raw.replace(_A_LINE, f"-- Output A Video Mode: Input4 , RES = {res}")
    raw = raw.replace(_B_LINE, f"-- Output B Video Mode: Input1 , RES = {res}, COPY OUTA MODE = OFF--")
    status = parse_status(raw)
    assert status.output_a.detail == SingleInputMode(4)
    assert status.output_a.resolution == res
    assert status.output_b.input == 1
    assert status.output_b.resolution == res
