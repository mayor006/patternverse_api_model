"""Patternverse MIS API — application entry point.

Wires together CORS, routers, the /health probe, and global error handling.
Run locally with:  uvicorn main:app --reload
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import get_settings
from models.schemas import HealthResponse
from routes import growth_area, mirror, patterns, session
from services.model_service import ModelUnavailableError
from services.supabase_service import StorageError, get_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("patternverse")

app = FastAPI(
    title="Patternverse Mirror Intelligence System API",
    description=(
        "A constrained mirror that surfaces the emotional and behavioral patterns "
        "a user keeps repeating — without advice, judgment, or premature conclusions."
    ),
    version="1.0.0",
)

# ── CORS — allow all origins for now ───────────────────────────────────
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────
app.include_router(mirror.router)
app.include_router(session.router)
app.include_router(patterns.router)
app.include_router(growth_area.router)


# ── Health ─────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment="production" if settings.is_production else "development",
        model=settings.model_backend,
    )


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {
        "name": "Patternverse Mirror Intelligence System API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


# ── Global error handling ──────────────────────────────────────────────
@app.exception_handler(ModelUnavailableError)
async def model_unavailable_handler(request: Request, exc: ModelUnavailableError):
    logger.error("Model backend unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(StorageError)
async def storage_error_handler(request: Request, exc: StorageError):
    logger.error("Storage error: %s", exc)
    return JSONResponse(
        status_code=500, content={"detail": f"Storage error: {exc}"}
    )


@app.on_event("startup")
async def _startup() -> None:
    settings = get_settings()
    store = get_store()
    logger.info(
        "Patternverse MIS API ready | env=%s | model=%s | storage=%s",
        settings.app_env,
        settings.model_backend,
        getattr(store, "backend_name", "unknown"),
    )
