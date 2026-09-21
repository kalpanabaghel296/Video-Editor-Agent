"""FastAPI application entrypoint for the AI Video Editing Agent."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.jobs import router as jobs_router
from app.api.metadata import router as metadata_router
from app.api.orchestration import router as orchestration_router
from app.api.planning import router as planning_router
from app.api.rendering import router as rendering_router
from app.api.scenes import router as scenes_router
from app.api.silence import router as silence_router
from app.api.transcription import router as transcription_router
from app.api.upload import router as upload_router

app = FastAPI(
    title="Video Editor Agent API",
    description=(
        "REST API for the AI-powered video editing agent pipeline. "
        "Handles video ingestion, autonomous job orchestration, and export delivery."
    ),
    version="0.5.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — permissive for local development; tighten in production
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(upload_router)
app.include_router(metadata_router)
app.include_router(scenes_router)
app.include_router(jobs_router)
app.include_router(transcription_router)
app.include_router(silence_router)
app.include_router(planning_router)
app.include_router(rendering_router)
app.include_router(orchestration_router)


# ---------------------------------------------------------------------------
# Built-in endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, str]:
    """Basic health endpoint for service availability checks."""
    return {"status": "ok"}
