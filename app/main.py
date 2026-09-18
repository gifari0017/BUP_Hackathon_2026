"""FastAPI application.

Endpoint names and response shapes follow the Problem Statement exactly. Error bodies are generic:
no secrets, no provider payloads, no stack traces.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.llm.registry import build_chain
from app import pipeline
from app.pipeline import (
    InfeasibleScenario,
    InterpretationUnavailable,
    PlanRejected,
    SolverFailure,
)
from app.schemas import HealthResponse, OptimizeRequest, OptimizeResponse

logger = logging.getLogger("gridwise")

#: Optional browser console for human testing. It is not part of the judged API surface.
INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app.state.settings = settings
    app.state.http = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
    app.state.chain = build_chain(settings, app.state.http)
    if not app.state.chain:
        # Names only. A value is never logged.
        logger.warning(
            "no language-model provider configured; set GRIDWISE_LLM_PROVIDER and the matching "
            "API key environment variable"
        )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    title="GridWise Energy Optimizer",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the demo console. Static file only: no configuration or credential reaches it."""
    return FileResponse(INDEX_HTML, media_type="text/html")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Readiness probe. Never calls a language model."""
    return HealthResponse(status="ok")


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(request: Request, payload: OptimizeRequest) -> OptimizeResponse:
    settings = request.app.state.settings
    return await pipeline.run(
        request.app.state.chain,
        payload,
        repair_attempts=settings.llm_repair_attempts,
        transport_retries=settings.llm_transport_retries,
    )


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Malformed JSON is 400; a well-formed body that breaks the schema is 422."""
    for error in exc.errors():
        if error.get("type") == "json_invalid":
            return _error(status.HTTP_400_BAD_REQUEST, "malformed JSON request body")
    return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "request does not match the required schema")


@app.exception_handler(InfeasibleScenario)
async def _infeasible(request: Request, exc: InfeasibleScenario) -> JSONResponse:
    logger.warning("scenario is infeasible under the interpreted directives: %s", exc)
    return _error(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "the operator directives admit no valid schedule for this scenario",
    )


@app.exception_handler(InterpretationUnavailable)
async def _interpretation_unavailable(
    request: Request, exc: InterpretationUnavailable
) -> JSONResponse:
    # Logged in full for diagnosis; the client sees a generic message.
    logger.error("interpretation unavailable: %s", exc)
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "operator-note interpretation is temporarily unavailable",
    )


@app.exception_handler(PlanRejected)
async def _plan_rejected(request: Request, exc: PlanRejected) -> JSONResponse:
    logger.error("generated plan failed the replay validator: %s", exc)
    return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal validation error")


@app.exception_handler(SolverFailure)
async def _solver_failure(request: Request, exc: SolverFailure) -> JSONResponse:
    logger.error("solver failure: %s", exc)
    return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal optimization error")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal server error")
