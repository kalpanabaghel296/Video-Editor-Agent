"""Pydantic models for job lifecycle — request, response, and internal state."""

from typing import Optional
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.media import MediaMetadata


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    """Possible states a job can be in throughout the pipeline."""

    PENDING = "pending"
    ANALYSING = "analysing"
    ANALYZING = "analysing"
    PLANNING = "planning"
    RENDERING = "rendering"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Job sub-folder layout (single source of truth)
# ---------------------------------------------------------------------------

JOB_SUBFOLDERS: list[str] = [
    "input",
    "metadata",
    "analysis",
    "planning",
    "rendering",
    "validation",
    "output",
]

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class UploadResponse(BaseModel):
    """Returned after a successful video upload."""

    job_id: UUID = Field(..., description="Unique identifier for this editing job.")
    status: JobStatus = Field(
        default=JobStatus.PENDING,
        description="Initial status of the job after upload.",
    )
    input_path: str = Field(
        ..., description="Relative path where the uploaded video was stored."
    )
    message: str = Field(default="Video uploaded successfully. Job is queued.")
    instruction: Optional[str] = Field(
        default=None, description="Optional user editing instruction."
    )
    metadata: Optional[MediaMetadata] = Field(
        default=None, description="Technical metadata extracted from the video."
    )


class JobStatusResponse(BaseModel):
    """Snapshot of a job's current status."""

    job_id: UUID = Field(..., description="Unique identifier for the editing job.")
    status: JobStatus = Field(..., description="Current status of the job.")
