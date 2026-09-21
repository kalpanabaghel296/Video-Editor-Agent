"""
Scene detection router.

Routes
------
POST /jobs/{job_id}/scenes/detect
    Run scene detection on the uploaded video and save scenes.json.

GET  /jobs/{job_id}/scenes
    Return the previously detected scenes.json.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.models.scene import SceneDetectionResult
from app.services.scene_detection import detect_scenes, load_scenes

router = APIRouter(prefix="/jobs", tags=["Scene Detection"])


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/scenes/detect
# ---------------------------------------------------------------------------


@router.post(
    "/{job_id}/scenes/detect",
    response_model=SceneDetectionResult,
    status_code=status.HTTP_200_OK,
    summary="Detect scene / shot boundaries in an uploaded video",
    description=(
        "Runs **PySceneDetect** on ``jobs/{job_id}/input/video.mp4`` and "
        "writes the result to ``jobs/{job_id}/analysis/scenes.json``. "
        "Supports two detectors: ``content`` (HSV diff, default) and "
        "``threshold`` (luminance). "
        "Returns the structured list of detected scenes."
    ),
)
def trigger_scene_detection(
    job_id: uuid.UUID,
    detector: Literal["content", "threshold"] = Query(
        default="content",
        description=(
            "Detection algorithm: ``content`` = HSV histogram diff "
            "(best for most footage), ``threshold`` = luminance cut detector."
        ),
    ),
    threshold: float | None = Query(
        default=None,
        description=(
            "Detection sensitivity. Defaults: 27.0 for content, 12.0 for threshold. "
            "Lower values = more sensitive (more scenes detected)."
        ),
        ge=0.0,
    ),
    min_scene_len: int = Query(
        default=15,
        description="Minimum number of frames a scene must span. Filters flash-cuts.",
        ge=1,
    ),
) -> SceneDetectionResult:
    """
    Trigger PySceneDetect for *job_id*.

    - **404** — input video not found (upload first).
    - **422** — bad query parameters.
    - **503** — PySceneDetect / OpenCV not installed.
    - **500** — unexpected processing error.
    """
    try:
        return detect_scenes(
            job_id,
            detector=detector,
            threshold=threshold,
            min_scene_len_frames=min_scene_len,
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        msg = str(exc)
        if "not installed" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=msg,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=msg,
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during scene detection: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/scenes
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/scenes",
    response_model=SceneDetectionResult,
    status_code=status.HTTP_200_OK,
    summary="Retrieve previously detected scenes",
    description=(
        "Returns the scene list stored in ``jobs/{job_id}/analysis/scenes.json``. "
        "Run ``POST /jobs/{job_id}/scenes/detect`` first if it does not exist."
    ),
)
def get_scenes(job_id: uuid.UUID) -> SceneDetectionResult:
    """
    Fetch persisted scene-detection results for *job_id*.

    - **404** — scenes.json not generated yet.
    - **422** — stored file is malformed.
    """
    try:
        return load_scenes(job_id)

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
            detail=f"Unexpected error loading scenes: {exc}",
        ) from exc
