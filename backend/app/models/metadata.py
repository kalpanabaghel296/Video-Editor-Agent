from typing import Optional
from pydantic import BaseModel


class VideoMetadata(BaseModel):
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    audio_sample_rate: Optional[int] = None
    size_bytes: int = 0
