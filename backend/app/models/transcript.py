"""Pydantic models for speech-to-text transcript data."""

from typing import Optional
from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    """A single timed segment returned by Whisper."""

    segment_id: int = Field(..., description="0-based segment index.")
    start: float = Field(..., description="Segment start time in seconds.")
    end: float = Field(..., description="Segment end time in seconds.")
    text: str = Field(..., description="Transcribed text for this segment.")


class TranscriptionResult(BaseModel):
    """Full transcription output, stored as transcript.json."""

    job_id: str = Field(..., description="UUID of the job.")
    language: Optional[str] = Field(None, description="Detected language code, e.g. 'en'.")
    full_text: str = Field(..., description="Complete transcript as a single string.")
    segments: list[TranscriptSegment] = Field(
        default_factory=list, description="Time-aligned transcript segments."
    )
    model_used: str = Field(
        ..., description="Whisper model size used, e.g. 'tiny' or 'base'."
    )
