"""
Transcription router — trigger Whisper speech-to-text and retrieve transcripts.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.models.transcript import TranscriptionResult
from app.services.transcription_service import load_transcript, transcribe

router = APIRouter(prefix="/jobs", tags=["Transcription"])


@router.post(
    "/{job_id}/transcribe",
    response_model=TranscriptionResult,
    status_code=status.HTTP_200_OK,
    summary="Transcribe video audio using Whisper",
    description=(
        "Extracts audio from ``jobs/{job_id}/input/video.mp4`` and runs local Whisper "
        "speech-to-text. Persists result to ``jobs/{job_id}/analysis/transcript.json``."
    ),
)
def trigger_transcription(
    job_id: uuid.UUID,
    model_size: str = Query(
        default="tiny",
        description="Whisper model size: tiny, base, small, medium, large.",
    ),
    language: Optional[str] = Query(
        default=None,
        description="Optional ISO language code (e.g. 'en'). Default is auto-detection.",
    ),
) -> TranscriptionResult:
    try:
        return transcribe(job_id, model_size=model_size, language=language)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transcription failed: {exc}",
        ) from exc


@router.get(
    "/{job_id}/transcript",
    response_model=TranscriptionResult,
    status_code=status.HTTP_200_OK,
    summary="Retrieve previously generated transcript",
    description="Returns the transcript stored in ``jobs/{job_id}/analysis/transcript.json``.",
)
def get_transcript(job_id: uuid.UUID) -> TranscriptionResult:
    try:
        return load_transcript(job_id)
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
            detail=f"Error loading transcript: {exc}",
        ) from exc
