"""
FastAPI service for controlling the HDMI matrix via HTTP API.
"""
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
import uvicorn

from hdmi_matrix import HDMIMatrix

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


@app.get('/health', response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok")


if __name__ == '__main__':
    # Run the FastAPI app with uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
