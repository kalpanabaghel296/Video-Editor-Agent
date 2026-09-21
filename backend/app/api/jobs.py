"""Jobs router — endpoints to inspect job status and metadata."""

import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.models.job import JobStatusResponse
from app.services.job_service import JOBS_ROOT, get_job_state

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get job status",
    description="Returns the current status of an editing job.",
)
def get_job_status(job_id: uuid.UUID) -> JobStatusResponse:
    """
    Fetch the current lifecycle status for *job_id*.

    Raises 404 if the job does not exist.
    """
    state = get_job_state(job_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return state


@router.get(
    "/{job_id}/video",
    summary="Stream uploaded video",
    description="Streams ``jobs/{job_id}/input/video.mp4`` for browser video playback.",
)
def get_job_video(job_id: uuid.UUID):
    video_path = JOBS_ROOT / str(job_id) / "input" / "video.mp4"
    if not video_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video not found for job '{job_id}'.",
        )
    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename="video.mp4",
    )

