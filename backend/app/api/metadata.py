"""
Metadata router — endpoints to trigger and retrieve video metadata extraction.

Routes
------
POST /jobs/{job_id}/metadata/extract
    Run ffprobe on the uploaded video and persist ``media.json``.

GET  /jobs/{job_id}/metadata
    Return the previously extracted ``media.json`` as a structured response.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.models.media import MediaMetadata
from app.services.metadata_service import extract_metadata, load_metadata

router = APIRouter(prefix="/jobs", tags=["Metadata"])


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/metadata/extract
# ---------------------------------------------------------------------------


@router.post(
    "/{job_id}/metadata/extract",
    response_model=MediaMetadata,
    status_code=status.HTTP_200_OK,
    summary="Extract and persist video metadata",
    description=(
        "Runs **ffprobe** on ``jobs/{job_id}/input/video.mp4``, extracts "
        "codec, resolution, FPS, duration, audio track info, and saves the "
        "result to ``jobs/{job_id}/metadata/media.json``. "
        "Returns the structured metadata object."
    ),
)
def trigger_metadata_extraction(job_id: uuid.UUID) -> MediaMetadata:
    """
    Trigger ffprobe extraction for *job_id* and return the metadata.

    Raises **404** if the input video is missing, **422** if the video is
    corrupt or unsupported, and **503** if ffprobe is not installed.
    """
    try:
        return extract_metadata(job_id)

    except FileNotFoundError as exc:
        msg = str(exc)
        if "ffprobe" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=msg,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=msg,
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during metadata extraction: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/metadata
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/metadata",
    response_model=MediaMetadata,
    status_code=status.HTTP_200_OK,
    summary="Retrieve previously extracted video metadata",
    description=(
        "Returns the structured metadata stored in "
        "``jobs/{job_id}/metadata/media.json``. "
        "Call ``POST /jobs/{job_id}/metadata/extract`` first if the file does not exist."
    ),
)
def get_metadata(job_id: uuid.UUID) -> MediaMetadata:
    """
    Fetch persisted metadata for *job_id*.

    Raises **404** if ``media.json`` has not been generated yet, and **422**
    if the stored file is malformed.
    """
    try:
        return load_metadata(job_id)

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
            detail=f"Unexpected error loading metadata: {exc}",
        ) from exc
