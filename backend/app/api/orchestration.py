"""
Orchestration router — provides endpoints for one-click autonomous editing,
iterative revision loops, and platform preset discovery.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status

from app.agents.orchestrator import revise_and_render, run_autonomous_pipeline
from app.editing.renderer import RenderResult
from app.models.orchestration import BUILTIN_PRESETS, AutoEditRequest, PlatformPreset, RevisionRequest

router = APIRouter(tags=["Autonomous Orchestrator & Presets"])


@router.get(
    "/presets",
    response_model=list[PlatformPreset],
    summary="List available platform presets",
    description="Returns pre-configured editing profiles (TikTok/Reels, Shorts, YouTube, Square Feed).",
)
def list_presets() -> list[PlatformPreset]:
    return list(BUILTIN_PRESETS.values())


@router.post(
    "/jobs/{job_id}/auto-edit",
    response_model=RenderResult,
    status_code=status.HTTP_200_OK,
    summary="Run full autonomous editing pipeline in a single step",
    description=(
        "Executes the full agent loop: Ingestion Check -> Analysis (Whisper/Silence/Scenes) -> "
        "AI Director Planning -> Frame-accurate Cutting, Social Reframing, Audio Ducking -> "
        "Quality Validation -> Final Export."
    ),
)
def auto_edit_job(
    job_id: uuid.UUID,
    request: Optional[AutoEditRequest] = None,
) -> RenderResult:
    try:
        return run_autonomous_pipeline(job_id, request or AutoEditRequest())
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Autonomous editing failed: {exc}",
        ) from exc


@router.post(
    "/jobs/{job_id}/revise",
    response_model=RenderResult,
    status_code=status.HTTP_200_OK,
    summary="Submit iterative feedback to revise and re-render the edit",
    description=(
        "Updates the Edit Decision List based on conversational feedback (e.g. shortening duration, "
        "dropping clips, adjusting music or aspect ratio) and re-renders without re-transcribing."
    ),
)
def revise_job(
    job_id: uuid.UUID,
    request: RevisionRequest,
) -> RenderResult:
    try:
        return revise_and_render(job_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Revision rendering failed: {exc}",
        ) from exc
