"""Pydantic models for video and audio metadata extracted via ffprobe."""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stream-level models
# ---------------------------------------------------------------------------


class VideoStreamInfo(BaseModel):
    """Metadata for a single video stream extracted by ffprobe."""

    codec_name: str = Field(..., description="Video codec identifier, e.g. 'h264'.")
    codec_long_name: str = Field(
        ..., description="Human-readable codec name, e.g. 'H.264 / AVC / MPEG-4 AVC'."
    )
    width: int = Field(..., description="Frame width in pixels.")
    height: int = Field(..., description="Frame height in pixels.")
    fps: float = Field(..., description="Frames per second (avg_frame_rate evaluated).")
    bit_rate: Optional[int] = Field(
        None, description="Stream bit-rate in bits/second, if available."
    )
    pix_fmt: Optional[str] = Field(
        None, description="Pixel format, e.g. 'yuv420p'."
    )
    duration: Optional[float] = Field(
        None, description="Stream duration in seconds, if available."
    )


class AudioStreamInfo(BaseModel):
    """Metadata for a single audio stream extracted by ffprobe."""

    codec_name: str = Field(..., description="Audio codec identifier, e.g. 'aac'.")
    codec_long_name: str = Field(
        ..., description="Human-readable codec name."
    )
    sample_rate: Optional[int] = Field(
        None, description="Audio sample rate in Hz."
    )
    channels: Optional[int] = Field(None, description="Number of audio channels.")
    channel_layout: Optional[str] = Field(
        None, description="Channel layout string, e.g. 'stereo'."
    )
    bit_rate: Optional[int] = Field(
        None, description="Audio stream bit-rate in bits/second, if available."
    )
    duration: Optional[float] = Field(
        None, description="Audio stream duration in seconds, if available."
    )


# ---------------------------------------------------------------------------
# Top-level media metadata model (stored as media.json)
# ---------------------------------------------------------------------------


class MediaMetadata(BaseModel):
    """
    Full metadata snapshot for an uploaded video file.

    Serialised to ``jobs/{job_id}/metadata/media.json`` after extraction.
    """

    job_id: UUID = Field(..., description="The job this metadata belongs to.")
    filename: str = Field(..., description="Original uploaded filename.")
    format_name: str = Field(
        ..., description="Container format(s), e.g. 'mov,mp4,m4a,3gp,3g2,mj2'."
    )
    format_long_name: str = Field(
        ..., description="Human-readable container name."
    )
    duration: float = Field(..., description="Total file duration in seconds.")
    size_bytes: int = Field(..., description="File size in bytes.")
    overall_bit_rate: Optional[int] = Field(
        None, description="Overall bit-rate in bits/second."
    )
    has_video: bool = Field(..., description="True when at least one video stream is present.")
    has_audio: bool = Field(..., description="True when at least one audio stream is present.")
    video_streams: list[VideoStreamInfo] = Field(
        default_factory=list, description="All video streams found in the file."
    )
    audio_streams: list[AudioStreamInfo] = Field(
        default_factory=list, description="All audio streams found in the file."
    )
