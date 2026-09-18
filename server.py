"""
FastAPI service for controlling the HDMI matrix via HTTP API.
"""
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from hdmi_matrix import HDMIMatrix
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
    mode: str
    input: int | None = None
    combination: int | None = None
    left: int | None = None
    right: int | None = None
    top: int | None = None
    bottom: int | None = None
    main: int | None = None
    small: int | None = None


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


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the lifespan of the FastAPI application."""
    # Startup
    global _matrix
    try:
        _matrix = HDMIMatrix()
        logger.info("Matrix connection initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize matrix: {str(e)}")
        raise

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


@app.post('/set-output-input', response_model=SuccessResponse)
def set_output_input(
    request: SetOutputInputRequest,
    matrix: HDMIMatrix = Depends(get_matrix),
    quick: bool = True,
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
        logger.info(f"Received request to set output {request.output} to input {request.input}")

        start_time = time.perf_counter()
        response = matrix.set_output_input(request.output, request.input, quick=quick)
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time

        logger.info(f"Set output {request.output} to input {request.input} (Elapsed time: {elapsed_time:.3f} seconds)")

        if response is None and quick:
            response = "(unknown)"

        return SuccessResponse(status="success", response=response)

    except ValueError as e:
        # Validation error from HDMIMatrix
        logger.error(f"Validation error from request: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Other errors (serial port, etc.)
        logger.error(f"Error setting output: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/status', responses={503: {"model": ErrorResponse}})
def get_status(matrix: HDMIMatrix = Depends(get_matrix)) -> dict:
    """
    Read the device's full status (STA) and return the routing state.

    Every call hits the device; nothing is cached. Returns 503 with
    `{"error": "..."}` if the serial read fails or the status cannot be parsed.
    """
    try:
        start_time = time.perf_counter()
        status = matrix.get_status()
        logger.debug(f"Read matrix status (Elapsed time: {time.perf_counter() - start_time:.3f} seconds)")
    except ValueError as e:
        logger.error(f"Could not parse matrix status: {str(e)}")
        return JSONResponse(status_code=503, content={"error": f"status parse failed: {str(e)}"})
    except Exception as e:
        logger.error(f"Could not read matrix status: {str(e)}")
        return JSONResponse(status_code=503, content={"error": f"status read failed: {str(e)}"})
    return _status_to_json(status)


@app.post('/set-output-a-mode', response_model=SuccessResponse)
def set_output_a_mode(
    request: SetOutputAModeRequest,
    matrix: HDMIMatrix = Depends(get_matrix),
) -> SuccessResponse:
    """
    Set output A's video mode. The body has the same shape as output A in
    `GET /status`, e.g. `{"mode": "pip", "main": 1, "small": 4}`.
    `{"mode": "single", "input": n}` routes input n to A full-screen.
    """
    mode = request.mode.lower()
    needed = _MODE_FIELDS.get(mode)
    if needed is None:
        raise HTTPException(status_code=400, detail=f"unknown mode {request.mode!r}")
    given = {name: value for name, value in request.model_dump().items()
             if name != "mode" and value is not None}
    missing = [name for name in needed if name not in given]
    extra = [name for name in given if name not in needed]
    if missing or extra:
        raise HTTPException(
            status_code=400,
            detail=f"mode {mode!r} needs {list(needed)}; missing {missing}, unexpected {extra}",
        )
    for name in needed:
        if given[name] not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail=f"{name} must be 1-4, got {given[name]!r}")

    try:
        logger.info(f"Received request to set output A mode to {mode} {given}")
        start_time = time.perf_counter()
        match mode:
            case "single":
                response = matrix.set_output_input("A", given["input"])
            case "2x2":
                response = matrix.set_output_a_2x2(given["combination"])
            case "2plr":
                response = matrix.set_output_a_side_by_side(given["left"], given["right"])
            case "2pud":
                response = matrix.set_output_a_top_bottom(given["top"], given["bottom"])
            case "1b3s":
                response = matrix.set_output_a_1big3small(given["combination"])
            case "pip":
                response = matrix.set_output_a_pip(given["main"], given["small"])
            case _:
                raise HTTPException(status_code=400, detail=f"unknown mode {mode!r}")
        elapsed_time = time.perf_counter() - start_time
        logger.info(f"Set output A mode to {mode} (Elapsed time: {elapsed_time:.3f} seconds)")
        return SuccessResponse(status="success", response=response or "")
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error from request: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error setting output A mode: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/health', response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok")


if __name__ == '__main__':
    # Run the FastAPI app with uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
