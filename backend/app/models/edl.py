"""EDL (Edit Decision List) — the single contract between planning and rendering.

The LLM / planner only ever produces this JSON. The renderer turns it into FFmpeg
commands that *we* build, so nothing unsafe can come out of the model.
"""
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

AspectRatio = Literal["16:9", "9:16", "1:1", "original"]


class SourceMedia(BaseModel):
    path: str
    duration: float
    width: int
    height: int
    fps: float = 30.0
    has_audio: bool = True


class Clip(BaseModel):
    id: str
    source_start: float
    source_end: float
    label: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start


class ProjectSettings(BaseModel):
    duration: float = 0.0
    aspect_ratio: AspectRatio = "original"
    width: int = 0
    height: int = 0
    fps: float = 30.0
    fit: Literal["crop", "pad"] = "crop"
    color_grade: bool = False


class SfxEvent(BaseModel):
    type: Literal["whoosh", "pop", "click", "ding"]
    time: float                  # timeline seconds in the OUTPUT video


class AudioSettings(BaseModel):
    sfx: bool = False
    sfx_events: List[SfxEvent] = []
    sfx_volume: float = 0.35
    keep_original: bool = True
    music: bool = False
    music_path: Optional[str] = None
    music_volume: float = 0.5          # relative to the (loudness-matched) music track; ducked under speech
    ducking: bool = True
    denoise: bool = False
    denoise_level: Literal["normal", "strong"] = "normal"
    music_style: Literal["neutral", "calm", "fast"] = "neutral"


class CaptionWord(BaseModel):
    text: str
    start: float
    end: float


class Caption(BaseModel):
    start: float                 # timeline time (seconds in the OUTPUT video)
    end: float
    text: str
    words: List[CaptionWord] = []


class CaptionSettings(BaseModel):
    enabled: bool = False
    style: Literal["plain", "styled"] = "styled"


class Effect(BaseModel):
    type: Literal["punch_in"] = "punch_in"
    clip_id: str
    zoom: float = 1.15


class Transition(BaseModel):
    """Transition INTO clip `clip_id` from the previous clip."""
    type: Literal["cut", "fade"] = "fade"
    clip_id: str
    duration: float = 0.3


class Overlay(BaseModel):
    """On-screen text (title card). Rendered through libass with a fade in/out."""
    type: Literal["text"] = "text"
    text: str
    start: float
    end: float
    position: Literal["top", "center", "bottom"] = "center"


class EDL(BaseModel):
    version: str = "1.0"
    source_media: SourceMedia
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    clips: List[Clip] = []
    audio: AudioSettings = Field(default_factory=AudioSettings)
    captions: List[Caption] = []
    caption_settings: CaptionSettings = Field(default_factory=CaptionSettings)
    effects: List[Effect] = []
    transitions: List[Transition] = []
    overlays: List[Overlay] = []
    explanation: List[str] = []
    requirements: Optional[Dict[str, Any]] = None

    def timeline_duration(self) -> float:
        return round(sum(max(c.duration, 0.0) for c in self.clips), 3)