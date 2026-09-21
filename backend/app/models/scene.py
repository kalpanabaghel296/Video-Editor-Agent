"""Pydantic models for scene / shot boundary detection results."""

from pydantic import BaseModel, Field


class SceneInfo(BaseModel):
    """A single detected scene / shot boundary."""

    scene_number: int = Field(
        ..., description="1-based index of the scene within the video."
    )
    start_time: float = Field(
        ..., description="Scene start time in seconds (from beginning of video)."
    )
    end_time: float = Field(
        ..., description="Scene end time in seconds (from beginning of video)."
    )
    duration: float = Field(
        ..., description="Scene duration in seconds (end_time - start_time)."
    )
    start_timecode: str = Field(
        ..., description="Human-readable start timecode, e.g. '00:00:12.500'."
    )
    end_timecode: str = Field(
        ..., description="Human-readable end timecode, e.g. '00:00:24.000'."
    )


class SceneDetectionResult(BaseModel):
    """Full scene-detection report for a job, stored as scenes.json."""

    job_id: str = Field(..., description="UUID of the job this result belongs to.")
    total_scenes: int = Field(..., description="Number of scenes detected.")
    detector: str = Field(
        ...,
        description="Detector algorithm used, e.g. 'ContentDetector(threshold=27.0)'.",
    )
    scenes: list[SceneInfo] = Field(
        default_factory=list, description="Ordered list of detected scenes."
    )
