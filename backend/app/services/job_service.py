import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.models.job import JOB_SUBFOLDERS, JobStatus, JobStatusResponse

# Root directory where all job artifacts live (relative to the repo root).
# Override via the JOBS_ROOT env-var if needed.
# Default: two levels above this file → project root → jobs/
# Override by setting the JOBS_ROOT environment variable.
_DEFAULT_JOBS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "jobs"
JOBS_ROOT = Path(os.getenv("JOBS_ROOT", str(_DEFAULT_JOBS_ROOT)))


def create_job_directories(job_id: uuid.UUID) -> Path:
    """
    Scaffold the standard sub-folder layout for a new job.

    Creates ``jobs/{job_id}/{subfolder}`` for every entry in
    :data:`~app.models.job.JOB_SUBFOLDERS`.

    Parameters
    ----------
    job_id:
        The UUID that uniquely identifies this job.

    Returns
    -------
    Path
        The root directory of the newly created job (``jobs/{job_id}``).
    """
    job_dir = JOBS_ROOT / str(job_id)
    for subfolder in JOB_SUBFOLDERS:
        (job_dir / subfolder).mkdir(parents=True, exist_ok=True)
    return job_dir


async def save_uploaded_video(job_id: uuid.UUID, file: UploadFile) -> Path:
    """
    Persist an uploaded video file to ``jobs/{job_id}/input/video.mp4``.

    Parameters
    ----------
    job_id:
        The UUID of the job this video belongs to.
    file:
        The :class:`~fastapi.UploadFile` received from the multipart request.

    Returns
    -------
    Path
        The absolute path where the video was saved.
    """
    job_dir = create_job_directories(job_id)
    destination = job_dir / "input" / "video.mp4"

    with destination.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return destination


def save_job_state(
    job_id: uuid.UUID,
    status: JobStatus,
    instruction: Optional[str] = None,
) -> Path:
    """
    Persist job status and metadata to ``jobs/{job_id}/state.json``.

    Parameters
    ----------
    job_id:
        The UUID of the job.
    status:
        Current JobStatus.
    instruction:
        Optional user editing instruction text.

    Returns
    -------
    Path
        Path to the saved state.json file.
    """
    job_dir = create_job_directories(job_id)
    state_path = job_dir / "state.json"
    state_data = {
        "job_id": str(job_id),
        "status": status.value,
        "instruction": instruction,
    }
    state_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")

    if instruction:
        instruction_path = job_dir / "instruction.txt"
        instruction_path.write_text(instruction, encoding="utf-8")

    return state_path


def get_job_state(job_id: uuid.UUID) -> Optional[JobStatusResponse]:
    """
    Retrieve job state from ``jobs/{job_id}/state.json``.

    Returns None if the job directory does not exist.
    """
    job_dir = JOBS_ROOT / str(job_id)
    if not job_dir.exists() or not job_dir.is_dir():
        return None

    state_path = job_dir / "state.json"
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            status_str = data.get("status", JobStatus.PENDING.value)
            return JobStatusResponse(job_id=job_id, status=JobStatus(status_str))
        except Exception:
            pass

    # Fallback for existing job folders lacking state.json
    return JobStatusResponse(job_id=job_id, status=JobStatus.PENDING)

