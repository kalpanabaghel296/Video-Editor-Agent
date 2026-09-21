"""
Rendering router — endpoints to trigger video rendering, quality validation,
and export/download output media.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.agents.validator import load_validation_report
from app.editing.audio import get_available_music_tracks
from app.editing.renderer import RenderResult, render_video
from app.models.validation import ValidationReport
from app.services.job_service import JOBS_ROOT

router = APIRouter(prefix="/jobs", tags=["Rendering & Export"])


@router.get(
    "/music/tracks",
    summary="List available royalty-free background music tracks",
    description="Returns available audio loop tracks for background music overlay.",
)
def list_music_tracks() -> list[dict[str, str]]:
    tracks = get_available_music_tracks()
    unique: dict[str, dict[str, str]] = {}
    for p in tracks.values():
        stem = p.stem
        if stem not in unique:
            unique[stem] = {
                "id": stem,
                "name": stem.replace("_", " ").title(),
                "filename": p.name,
            }
    return list(unique.values())


@router.post(
    "/{job_id}/render",
    response_model=RenderResult,
    status_code=status.HTTP_200_OK,
    summary="Render final video based on EDL with optional social framing & audio design",
    description=(
        "Executes single-pass FFmpeg cutting and concatenation. Supports 9:16 vertical "
        "blurred-background framing, background music with sidechain ducking, and subtitle burning."
    ),
)
def trigger_render(
    job_id: uuid.UUID,
    burn_subtitles: bool = Query(
        default=False,
        description="If True, burns re-timed subtitles directly into the video stream.",
    ),
    aspect_ratio: str = Query(
        default="original",
        pattern="^(original|9:16|1:1)$",
        description="Target social format: 'original', '9:16' (Shorts/Reels), or '1:1' (Square).",
    ),
    bg_music: Optional[str] = Query(
        default=None,
        description="Optional background music key ('chill', 'energetic', or track name).",
    ),
    music_volume: float = Query(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Background music volume level (0.0 to 1.0, default 0.15).",
    ),
) -> RenderResult:
    try:
        return render_video(
            job_id,
            burn_subtitles=burn_subtitles,
            aspect_ratio=aspect_ratio,
            bg_music=bg_music,
            music_volume=music_volume,
        )
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
            detail=f"Rendering failed: {exc}",
        ) from exc


@router.get(
    "/{job_id}/output/video",
    summary="Stream or download the rendered final video",
    description="Returns the rendered MP4 file from ``jobs/{job_id}/output/final_edit.mp4``.",
)
def get_rendered_video(job_id: uuid.UUID) -> FileResponse:
    output_video = JOBS_ROOT / str(job_id) / "output" / "final_edit.mp4"
    if not output_video.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rendered video not found for job '{job_id}'. Run render first.",
        )
    return FileResponse(
        path=str(output_video),
        media_type="video/mp4",
        filename="final_edit.mp4",
    )


@router.get(
    "/{job_id}/output/subtitles",
    summary="Download generated subtitles (.srt)",
    description="Returns re-timed subtitles from ``jobs/{job_id}/output/subtitles.srt``.",
)
def get_subtitles(job_id: uuid.UUID) -> FileResponse:
    output_srt = JOBS_ROOT / str(job_id) / "output" / "subtitles.srt"
    if not output_srt.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subtitles not found for job '{job_id}'.",
        )
    return FileResponse(
        path=str(output_srt),
        media_type="text/plain; charset=utf-8",
        filename="subtitles.srt",
    )


@router.get(
    "/{job_id}/validation",
    response_model=ValidationReport,
    summary="Retrieve automated quality validation report",
    description="Returns quality inspection report from ``jobs/{job_id}/validation/report.json``.",
)
def get_validation(job_id: uuid.UUID) -> ValidationReport:
    try:
        return load_validation_report(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load validation report: {exc}",
        ) from exc
