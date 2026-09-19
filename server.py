"""
FastAPI service for controlling the HDMI matrix via HTTP API.
"""
import base64
import os
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
import uvicorn

from hdmi_matrix import HDMIMatrix, CommandRejected, CommandTimeout
from status_cache import StatusCache, VersionConflict
from hdmi_matrix_status import (
    HDMIMatrixStatus,
    OneBigThreeSmallMode,
    OutputAStatus,
    PIPMode,
    SideBySideMode,
    SingleInputMode,
    TopBottomMode,
    TwoByTwoMode,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global matrix instance (can be enhanced to support multiple connections)
_matrix: HDMIMatrix | None = None

# Browsing status is not a reason to keep the matrix busy. Independent clients
# share this observation; explicit verification opts into a shorter max_age.
STATUS_CACHE_TTL = 5.0

_status_cache: StatusCache[HDMIMatrixStatus] | None = None

# Commands the raw-capture endpoint will not send. Both are recoverable only
# with physical access: SPCRSB changes the device's baud rate, leaving the
# service unable to talk to it at all, and SPCDF wipes the configuration.
# Send them with main.py if they are ever actually wanted.
RAW_COMMAND_DENIED = ('SPCRSB', 'SPCDF')

# Longest a raw capture may hold the serial port, which it does exclusively.
RAW_COMMAND_MAX_SECONDS = 30.0


class SetOutputInputRequest(BaseModel):
    """Request model for setting output to input."""
    output: str  # "A" or "B"
    input: int | str  # 1, 2, 3, 4 (or "U"/"D" to step up/down)


class SuccessResponse(BaseModel):
    """Success response model."""
    status: str
    response: str


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str


class SetOutputAModeRequest(BaseModel):
    """
    Request model for setting output A's video mode. Exactly the fields the
    mode needs must be present, all in 1-4:

    - single: input
    - 2x2, 1b3s: combination
    - 2plr: left, right
    - 2pud: top, bottom
    - pip: main, small
    """
    model_config = ConfigDict(extra="forbid")
    resolution: str | None = None
    mode: str
    input: StrictInt | None = None
    combination: StrictInt | None = None
    left: StrictInt | None = None
    right: StrictInt | None = None
    top: StrictInt | None = None
    bottom: StrictInt | None = None
    main: StrictInt | None = None
    small: StrictInt | None = None


# Which fields each output A mode needs, keyed by the lowercase mode name
# used on the wire.
_MODE_FIELDS: dict[str, tuple[str, ...]] = {
    "single": ("input",),
    "2x2": ("combination",),
    "2plr": ("left", "right"),
    "2pud": ("top", "bottom"),
    "1b3s": ("combination",),
    "pip": ("main", "small"),
}


def _output_a_to_json(output_a: OutputAStatus) -> dict:
    """Render output A's status in the wire shape: lowercase mode name plus
    the fields that mode uses."""
    detail = output_a.detail
    body: dict = {"mode": output_a.mode.value.lower()}
    match detail:
        case SingleInputMode(input=inp):
            body["input"] = inp
        case TwoByTwoMode(combination=c) | OneBigThreeSmallMode(combination=c):
            body["combination"] = c
        case SideBySideMode(left=left, right=right):
            body["left"] = left
            body["right"] = right
        case TopBottomMode(top=top, bottom=bottom):
            body["top"] = top
            body["bottom"] = bottom
        case PIPMode(main=main, small=small):
            body["main"] = main
            body["small"] = small
    body["resolution"] = output_a.resolution
    return body


def _status_to_json(status: HDMIMatrixStatus) -> dict:
    """Render the parts of the device status that clients need."""
    return {
        "outputs": {
            "A": _output_a_to_json(status.output_a),
            "B": {
                "input": status.output_b.input,
                "copy_a": status.output_b.copy_outa,
                "resolution": status.output_b.resolution,
            },
        },
        "inputs": {
            str(i + 1): {"linked": linked}
            for i, linked in enumerate(status.input_links)
        },
    }


class RawCommandRequest(BaseModel):
    """
    Request model for a raw capture. Defaults match how the service drives the
    device, so the common case is just `{"commands": [...]}`.
    """
    commands: list[str]
    gap: float = 0.1
    read_seconds: float = 1.5
    reset_input: bool = True


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the lifespan of the FastAPI application."""
    # Startup
    global _matrix, _status_cache
    try:
        matrix = HDMIMatrix()
    except Exception as e:
        logger.error(f"Failed to initialize matrix: {str(e)}")
        raise
    _matrix = matrix
    _status_cache = StatusCache(matrix.get_status, ttl=STATUS_CACHE_TTL)
    logger.info("Matrix connection initialized successfully")

    yield

    # Shutdown
    if _matrix is not None:
        try:
            _matrix.close()
            logger.info("Matrix connection closed")
        except Exception as e:
            logger.error(f"Error closing matrix connection: {str(e)}")


app = FastAPI(
    title="HDMI Matrix Control API",
    description="API for controlling the PORTTA 4x2 HDMI matrix switch",
    version="0.1.0",
    lifespan=lifespan
)


def get_matrix() -> HDMIMatrix:
    """Dependency for getting the matrix connection."""
    global _matrix
    if _matrix is None:
        raise HTTPException(
            status_code=500,
            detail="Matrix not initialized"
        )
    return _matrix


def get_status_cache() -> StatusCache[HDMIMatrixStatus]:
    """Dependency for getting the status cache."""
    global _status_cache
    if _status_cache is None:
        raise HTTPException(
            status_code=500,
            detail="Matrix not initialized"
        )
    return _status_cache


@app.post('/set-output-input', response_model=SuccessResponse)
def set_output_input(
    request: SetOutputInputRequest,
    matrix: HDMIMatrix = Depends(get_matrix),
    cache: StatusCache[HDMIMatrixStatus] = Depends(get_status_cache),
) -> SuccessResponse:
    """
    Set a video output to a single input.

    **Request:**
    - `output`: "A" or "B"
    - `input`: 1, 2, 3, or 4 (or "U"/"D" to step up/down)

    **Response:**
    - `status`: "success"
    - `response`: device response string
    """
    try:
        output = HDMIMatrix._validate_output(request.output)
        inp = HDMIMatrix._validate_input(request.input)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _execute([f'SPO{output}SI{inp}'], matrix, cache)


@app.get('/status', responses={503: {"model": ErrorResponse}})
def get_status(
    cache: StatusCache[HDMIMatrixStatus] = Depends(get_status_cache),
    max_age: float | None = Query(default=None, ge=0, allow_inf_nan=False),
) -> dict:
    """
    Read the device's status (STA) and return the routing state.

    Readings are cached for 5 seconds by default. `max_age` (seconds) lets
    explicit refreshes and verification request fresher data. Callers arriving during a
    read share its result, so several polling clients cost the serial port no
    more than one. A command invalidates the cache, so the reading after a
    switch always comes from the device. Returns 503 with `{"error": "..."}`
    if the serial read fails or the status cannot be parsed.
    """
    try:
        start_time = time.perf_counter()
        observation = cache.observation(max_age=max_age)
        logger.debug(f"Read matrix status (Elapsed time: {time.perf_counter() - start_time:.3f} seconds)")
    except ValueError as e:
        logger.error(f"Could not parse matrix status: {str(e)}")
        return JSONResponse(status_code=503, content={"error": f"status parse failed: {str(e)}"})
    except Exception as e:
        logger.error(f"Could not read matrix status: {str(e)}")
        return JSONResponse(status_code=503, content={"error": f"status read failed: {str(e)}"})
    return {
        **_status_to_json(observation.value),
        "observed_at": observation.observed_at.isoformat(),
        "age_ms": cache.age_ms(observation),
        "version": observation.version,
    }


@app.post('/set-output-a-mode', response_model=SuccessResponse)
def set_output_a_mode(
    request: SetOutputAModeRequest,
    matrix: HDMIMatrix = Depends(get_matrix),
    cache: StatusCache[HDMIMatrixStatus] = Depends(get_status_cache),
) -> SuccessResponse:
    """
    Set output A's video mode. The body has the same shape as output A in
    `GET /status`, e.g. `{"mode": "pip", "main": 1, "small": 4}`.
    `{"mode": "single", "input": n}` routes input n to A full-screen.
    """
    return _execute([_mode_command(request)], matrix, cache)


def _mode_command(request: SetOutputAModeRequest) -> str:
    mode = request.mode.lower()
    needed = _MODE_FIELDS.get(mode)
    if needed is None:
        raise HTTPException(status_code=400, detail=f"unknown mode {request.mode!r}")
    given = {name: value for name, value in request.model_dump().items()
             if name not in ("mode", "resolution") and value is not None}
    if set(given) != set(needed):
        raise HTTPException(status_code=400, detail=f"mode {mode!r} needs exactly {list(needed)}")
    for name in needed:
        if given[name] not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail=f"{name} must be 1-4")
    if mode == 'single':
        return f"SPOASI{given['input']:02d}"
    return 'SPOA' + mode.upper() + ''.join(str(given[name]) for name in needed)


def _execute(commands: list[str], matrix: HDMIMatrix,
             cache: StatusCache[HDMIMatrixStatus], if_version: int | None = None) -> SuccessResponse:
    try:
        with cache.command(if_version):
            response = matrix.send_commands(commands)
        return SuccessResponse(status="success", response=response)
    except VersionConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except CommandTimeout as e:
        raise HTTPException(status_code=504, detail=str(e))
    except CommandRejected as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("Matrix command failed")
        raise HTTPException(status_code=500, detail=str(e))


class ApplyBRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: StrictInt = Field(ge=1, le=4)
    resolution: str | None = None  # read-only; accepted when reusing routing


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    A: SetOutputAModeRequest | StrictInt | None = None
    B: ApplyBRequest | StrictInt | None = None
    if_version: StrictInt | None = Field(default=None, ge=0)


@app.post('/apply', response_model=SuccessResponse)
def apply_outputs(
    request: ApplyRequest,
    matrix: HDMIMatrix = Depends(get_matrix),
    cache: StatusCache[HDMIMatrixStatus] = Depends(get_status_cache),
) -> SuccessResponse:
    """Apply one or both routes with paced writes and fully drained replies.

    Accepts {"A": 2, "B": 4} or explicit mode/input objects. copy_a is not
    writable here. if_version rejects stale observations with 409 before any
    writes. Success acknowledges commands; /status still verifies application.
    """
    commands = []
    if request.A is not None:
        a = request.A
        if isinstance(a, int):
            a = SetOutputAModeRequest(mode='single', input=a)
        commands.append(_mode_command(a))
    if request.B is not None:
        inp = request.B if isinstance(request.B, int) else request.B.input
        if inp not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail="B input must be 1-4")
        commands.append(f'SPOBSI{inp:02d}')
    if not commands:
        raise HTTPException(status_code=400, detail="at least one of A or B is required")
    return _execute(commands, matrix, cache, request.if_version)


def raw_command_enabled() -> bool:
    """
    Whether the raw-capture endpoint is available.

    Read per request rather than at import so the flag can be flipped in tests
    and so a deploy that sets it does not depend on import order.
    """
    return os.environ.get('HDMI_MATRIX_ENABLE_RAW_COMMAND', '').lower() in ('1', 'true', 'yes', 'on')


def _render(data: bytes) -> str:
    """Render device bytes with the framing visible."""
    return (
        data.decode('ascii', errors='replace')
        .replace('\\', '\\\\')
        .replace('\r', '\\r')
        .replace('\n', '\\n')
    )


@app.post('/debug/raw-command')
def raw_command(
    request: RawCommandRequest,
    matrix: HDMIMatrix = Depends(get_matrix),
    cache: StatusCache[HDMIMatrixStatus] = Depends(get_status_cache),
) -> dict:
    """
    Write raw commands to the device and return every byte it sends back, with
    arrival times. Disabled unless `HDMI_MATRIX_ENABLE_RAW_COMMAND` is set;
    otherwise it 404s like any other unrouted path.

    This exists to answer questions the normal endpoints cannot, because they
    impose the framing that is in question: what the device does with commands
    sent back to back, how long it defers a reply, and whether two replies
    interleave. Spaces in a command are stripped, as elsewhere, so
    `"spob copy outa on"` works.

    **Response:**
    - `events`: in order, each with `at_ms` (milliseconds since the first
      write) and `kind`. A `write` event carries `command`; a `read` event
      carries `bytes`, `text` (with `\r`/`\n` escaped) and `base64`.
    """
    if not raw_command_enabled():
        raise HTTPException(status_code=404, detail="Not Found")

    commands = [c.replace(' ', '').strip() for c in request.commands]
    if not commands:
        raise HTTPException(status_code=400, detail="commands must not be empty")
    for command in commands:
        if not command:
            raise HTTPException(status_code=400, detail="commands must not contain a blank entry")
        if not command.isascii() or not command.isprintable():
            raise HTTPException(status_code=400, detail=f"command {command!r} must be printable ASCII")
        if command.upper().startswith(RAW_COMMAND_DENIED):
            raise HTTPException(
                status_code=400,
                detail=f"command {command!r} is not allowed here; it needs physical access to undo",
            )
    if request.gap < 0 or request.read_seconds <= 0:
        raise HTTPException(status_code=400, detail="gap must be >= 0 and read_seconds > 0")
    budget = request.gap * (len(commands) - 1) + request.read_seconds
    if budget > RAW_COMMAND_MAX_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"capture would hold the port for {budget:.1f}s, over the {RAW_COMMAND_MAX_SECONDS:.0f}s limit",
        )

    logger.warning("Raw capture: sending %s", commands)
    try:
        with cache.command():
            events = matrix.send_raw(
                commands,
                gap=request.gap,
                read_seconds=request.read_seconds,
                reset_input=request.reset_input,
            )
    except Exception as e:
        logger.error(f"Raw capture failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    rendered = []
    for event in events:
        if event['kind'] == 'read':
            data = event['data']
            rendered.append({
                'at_ms': event['at_ms'],
                'kind': 'read',
                'bytes': len(data),
                'text': _render(data),
                'base64': base64.b64encode(data).decode('ascii'),
            })
        else:
            rendered.append(event)
    return {'events': rendered}


@app.get('/health', response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok")


if __name__ == '__main__':
    # Run the FastAPI app with uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
