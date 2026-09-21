"""
Pydantic data models for autonomous orchestration, platform presets,
and iterative revision workflows.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PipelineStage(str, Enum):
    IDLE = "idle"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    RENDERING = "rendering"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


class PlatformPreset(BaseModel):
    preset_id: str
    name: str
    description: str
    aspect_ratio: str = "original"
    burn_subtitles: bool = True
    enable_punch_in: bool = False
    bg_music: Optional[str] = None
    music_volume: float = 0.15
    target_duration: Optional[float] = None


BUILTIN_PRESETS: dict[str, PlatformPreset] = {
    "reels": PlatformPreset(
        preset_id="reels",
        name="Instagram Reels and TikTok",
        description="9:16 Vertical, punch-in zoom on emphasis cuts, burned captions, energetic background music.",
        aspect_ratio="9:16",
        burn_subtitles=True,
        enable_punch_in=True,
        bg_music="upbeat_energetic",
        music_volume=0.15,
        target_duration=30.0,
    ),
    "shorts": PlatformPreset(
        preset_id="shorts",
        name="YouTube Shorts",
        description="9:16 Vertical, punch-in zoom, burned captions, calm lo-fi background music.",
        aspect_ratio="9:16",
        burn_subtitles=True,
        enable_punch_in=True,
        bg_music="chill_ambient",
        music_volume=0.14,
        target_duration=30.0,
    ),
    "youtube": PlatformPreset(
        preset_id="youtube",
        name="YouTube Standard (16:9)",
        description="Preserve 16:9 canvas, clean silence removal, subtle ambient background music.",
        aspect_ratio="original",
        burn_subtitles=False,
        enable_punch_in=False,
        bg_music="chill_ambient",
        music_volume=0.10,
        target_duration=None,
    ),
    "square": PlatformPreset(
        preset_id="square",
        name="Square Feed (1:1)",
        description="1:1 Canvas with blurred borders for LinkedIn and Instagram feed posts, styled subtitles.",
        aspect_ratio="1:1",
        burn_subtitles=True,
        enable_punch_in=False,
        bg_music="chill_ambient",
        music_volume=0.12,
        target_duration=45.0,
    ),
}


class AutoEditRequest(BaseModel):
    preset: Optional[str] = Field(
        default=None,
        description="Preset ID ('reels', 'shorts', 'youtube', 'square') to use as base profile.",
    )
    instruction: Optional[str] = Field(
        default="",
        description="Natural language editing instruction.",
    )
    target_duration: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Optional target length in seconds.",
    )
    aspect_ratio: Optional[str] = Field(
        default=None,
        description="Target aspect ratio ('original', '9:16', '1:1'). Overrides preset if set.",
    )
    burn_subtitles: Optional[bool] = Field(
        default=None,
        description="Whether to burn captions directly onto video pixels. Overrides preset if set.",
    )
    enable_punch_in: Optional[bool] = Field(
        default=None,
        description="Whether to apply subtle 1.15x punch-in zoom on cuts. Overrides preset if set.",
    )
    bg_music: Optional[str] = Field(
        default=None,
        description="Background music track key or 'none'. Overrides preset if set.",
    )
    music_volume: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Background music volume (0.0 to 1.0). Overrides preset if set.",
    )


class RevisionRequest(BaseModel):
    revision_instruction: str = Field(
        ...,
        description="Feedback or revision instruction.",
    )
    target_duration: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="New target duration if changed.",
    )
    aspect_ratio: Optional[str] = Field(
        default=None,
        description="New aspect ratio if changed.",
    )
    burn_subtitles: Optional[bool] = Field(
        default=None,
        description="New subtitle burn flag if changed.",
    )
    enable_punch_in: Optional[bool] = Field(
        default=None,
        description="New punch-in zoom flag if changed.",
    )
    bg_music: Optional[str] = Field(
        default=None,
        description="New music track key or 'none' if changed.",
    )
    music_volume: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="New music volume if changed.",
    )
