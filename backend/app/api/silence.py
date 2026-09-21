"""
Silence detection router — endpoints to trigger and retrieve silence detection.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.services.silence_detection import (
    SilenceDetectionResult,
    detect_silence,
    load_silence,
)

router = APIRouter(prefix="/jobs", tags=["Silence Detection"])


@router.post(
    "/{job_id}/silence/detect",
    response_model=SilenceDetectionResult,
    status_code=status.HTTP_200_OK,
    summary="Detect silent segments in uploaded video audio",
    description=(
        "Runs FFmpeg ``silencedetect`` on ``jobs/{job_id}/input/video.mp4`` and saves "
        "the resulting silence intervals to ``jobs/{job_id}/analysis/silence.json``."
    ),
)
def trigger_silence_detection(
    job_id: uuid.UUID,
    noise_db: float = Query(
        default=-26.0,
        description="Noise threshold in dB below which audio is considered silent (e.g. -26.0).",
    ),
    min_duration: float = Query(
        default=0.3,
        description="Minimum silence interval in seconds to detect (e.g. 0.3).",
        ge=0.1,
    ),
) -> SilenceDetectionResult:
    try:
        return detect_silence(job_id, noise_db=noise_db, min_duration=min_duration)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Silence detection failed: {exc}",
        ) from exc


@router.get(
    "/{job_id}/silence",
    response_model=SilenceDetectionResult,
    status_code=status.HTTP_200_OK,
    summary="Retrieve previously detected silence segments",
    description="Returns silence report stored in ``jobs/{job_id}/analysis/silence.json``.",
)
def get_silence(job_id: uuid.UUID) -> SilenceDetectionResult:
    try:
        return load_silence(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error loading silence report: {exc}",
        ) from exc
