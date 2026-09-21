"""Upload router — exposes POST /upload to ingest a video and create a new job."""

import logging
import uuid
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.models.job import JobStatus, UploadResponse
from app.services.job_service import save_job_state, save_uploaded_video
from app.services.metadata_service import extract_metadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["Upload"])

# Allowed MIME types and extensions for uploaded videos
_ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/x-msvideo",
    "video/webm",
    "video/x-matroska",
    "application/octet-stream",  # Some clients send this for .mp4
}

_ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".mpeg", ".mpg"}


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a video file to start a new editing job",
    description=(
        "Accepts a video file and optional editing instruction via multipart/form-data, "
        "assigns a unique job ID, scaffolds the job directory layout under ``jobs/{job_id}/``, "
        "saves the video to ``jobs/{job_id}/input/video.mp4``, automatically extracts "
        "technical metadata via ffprobe to ``jobs/{job_id}/metadata/media.json``, and "
        "returns the job ID, status, and metadata snapshot."
    ),
)
async def upload_video(
    file: UploadFile = File(..., description="Video file to be processed."),
    instruction: Optional[str] = Form(
        default=None, description="Optional user editing instruction."
    ),
) -> UploadResponse:
    """
    Handle video upload, persist video, extract metadata, and initialize job state.
    """
    # --- Content-type / extension validation -------------------------------
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if file.content_type not in _ALLOWED_CONTENT_TYPES and ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{file.content_type}'. "
                "Please upload a supported video file (.mp4, .mov, .avi, .webm, .mkv, .mpeg)."
            ),
        )

    # --- Generate unique job ID --------------------------------------------
    job_id = uuid.uuid4()

    # --- Persist the file --------------------------------------------------
    try:
        saved_path = await save_uploaded_video(job_id, file)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded video: {exc}",
        ) from exc

    # --- Automatically extract metadata ------------------------------------
    metadata = None
    job_status = JobStatus.PENDING
    message = "Video uploaded and metadata extracted successfully. Job is queued."

    try:
        metadata = extract_metadata(job_id)
    except Exception as exc:
        logger.warning(
            "Automatic metadata extraction failed for job %s: %s", job_id, exc
        )
        message = (
            f"Video uploaded successfully, but metadata extraction encountered an issue: {exc}"
        )

    # --- Persist initial job state -----------------------------------------
    try:
        save_job_state(job_id=job_id, status=job_status, instruction=instruction)
    except Exception as exc:
        logger.warning("Failed to save job state for job %s: %s", job_id, exc)

    return UploadResponse(
        job_id=job_id,
        status=job_status,
        input_path=str(saved_path),
        message=message,
        instruction=instruction,
        metadata=metadata,
    )

