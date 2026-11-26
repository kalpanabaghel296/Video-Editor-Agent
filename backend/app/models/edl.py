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


LookPreset = Literal["none", "cinematic", "warm", "cool", "bw", "noir", "vintage", "vivid", "faded"]


class Look(BaseModel):
    """Colour / style of the whole video. All sliders are -100..100 (sharpen / vignette / grain 0..100)."""
    preset: LookPreset = "none"
    brightness: float = Field(0.0, ge=-100, le=100)
    contrast: float = Field(0.0, ge=-100, le=100)
    saturation: float = Field(0.0, ge=-100, le=100)
    warmth: float = Field(0.0, ge=-100, le=100)        # -100 cool ... +100 warm
    sharpen: float = Field(0.0, ge=0, le=100)
    vignette: float = Field(0.0, ge=0, le=100)
    grain: float = Field(0.0, ge=0, le=100)
    fade_in_out: bool = False                           # fade from / to black
    letterbox: bool = False                             # cinema bars
    mirror: bool = False                                # horizontal flip

    @property
    def is_default(self) -> bool:
        return self == Look()

    def describe(self) -> str:
        parts = [] if self.preset == "none" else [self.preset.upper()]
        for k in ("brightness", "contrast", "saturation", "warmth"):
            v = getattr(self, k)
            if v:
                parts.append(f"{k} {v:+.0f}")
        for k in ("sharpen", "vignette", "grain"):
            v = getattr(self, k)
            if v:
                parts.append(f"{k} {v:.0f}")
        parts += [n for n, on in (("fade in/out", self.fade_in_out), ("letterbox", self.letterbox), ("mirror", self.mirror)) if on]
        return ", ".join(parts)


class ProjectSettings(BaseModel):
    duration: float = 0.0
    aspect_ratio: AspectRatio = "original"
    width: int = 0
    height: int = 0
    fps: float = 30.0
    fit: Literal["crop", "pad", "blur"] = "crop"       # crop = fill the frame, pad = black bars, blur = blurred copy behind
    color_grade: bool = False
    look: Look = Field(default_factory=Look)


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


CaptionPreset = Literal["classic", "hormozi", "minimal", "boxed", "karaoke"]


class CaptionSettings(BaseModel):
    enabled: bool = False
    style: Literal["plain", "styled"] = "styled"
    preset: CaptionPreset = "classic"                  # look of the burned-in captions (editing/captions.py)
    size: Literal["small", "normal", "large"] = "normal"
    margin_pct: Optional[float] = None                 # distance from the bottom as a fraction of the height (platform safe zone)
    max_words: Optional[int] = None                    # words per caption block (None = preset default)
    font: Optional[str] = None                         # override; default is picked per preset / script (Devanagari -> Nirmala UI)
    language: Optional[str] = None                     # caption language code (hi, kn, ta, te, bn, es, en); None = as spoken


class Effect(BaseModel):
    type: Literal["punch_in"] = "punch_in"
    clip_id: str
    zoom: float = 1.15


class Transition(BaseModel):
    """Transition INTO clip `clip_id` from the previous clip."""
    type: Literal["cut", "fade"] = "fade"
    clip_id: str
    duration: float = 0.3


class Reframe(BaseModel):
    """Smart-crop path of one clip (face-aware reframing): where the crop window sits over time.
    points = [[seconds from the start of the clip, centre of the window as a 0..1 fraction of the frame]]."""
    clip_id: str
    axis: Literal["x", "y"] = "x"                      # x: the video is wider than the target (landscape -> 9:16)
    points: List[List[float]] = []
    face_ratio: float = 0.0                            # share of sampled frames in which a face was found


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
    reframes: List[Reframe] = []
    explanation: List[str] = []
    warnings: List[str] = []                           # shown to the user (e.g. "source is shorter than the requested length")
    requirements: Optional[Dict[str, Any]] = None

    def timeline_duration(self) -> float:
        return round(sum(max(c.duration, 0.0) for c in self.clips), 3)