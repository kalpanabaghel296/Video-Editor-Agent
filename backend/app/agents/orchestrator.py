"""
Autonomous Orchestrator Agent — coordinates the complete end-to-end video editing pipeline.
Executes the full agent loop:
OBSERVE (Media Analysis) → UNDERSTAND & PLAN (Director EDL) → ACT (Cutting, Reframing, Audio Ducking) → INSPECT (Quality Validation)
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from app.agents.director import plan_edit, revise_edl
from app.editing.renderer import RenderResult, render_video
from app.models.edl import EditDecisionList
from app.models.job import JobStatus
from app.models.orchestration import BUILTIN_PRESETS, AutoEditRequest, RevisionRequest
from app.services.job_service import JOBS_ROOT, save_job_state
from app.services.metadata_service import load_metadata
from app.services.scene_detection import detect_scenes
from app.services.silence_detection import detect_silence
from app.services.transcription_service import transcribe

logger = logging.getLogger(__name__)


def run_autonomous_pipeline(
    job_id: uuid.UUID,
    request: Optional[AutoEditRequest] = None,
) -> RenderResult:
    """
    Execute the entire video editing lifecycle autonomously in a single coordinated pipeline.
    """
    if request is None:
        request = AutoEditRequest()

    job_dir = JOBS_ROOT / str(job_id)
    if not job_dir.exists():
        raise FileNotFoundError(f"Job '{job_id}' not found.")

    # 1. Resolve Preset and Configuration Overrides
    preset_config = BUILTIN_PRESETS.get(request.preset or "")

    aspect_ratio = (
        request.aspect_ratio
        if request.aspect_ratio is not None
        else (preset_config.aspect_ratio if preset_config else "original")
    )
    burn_subtitles = (
        request.burn_subtitles
        if request.burn_subtitles is not None
        else (preset_config.burn_subtitles if preset_config else True)
    )
    enable_punch_in = (
        request.enable_punch_in
        if request.enable_punch_in is not None
        else (preset_config.enable_punch_in if preset_config else False)
    )
    bg_music = (
        request.bg_music
        if request.bg_music is not None
        else (preset_config.bg_music if preset_config else None)
    )
    music_volume = (
        request.music_volume
        if request.music_volume is not None
        else (preset_config.music_volume if preset_config else 0.15)
    )

    # Build effective instruction
    instruction = (request.instruction or "").strip()
    target_duration = (
        request.target_duration
        if request.target_duration is not None
        else (preset_config.target_duration if preset_config else None)
    )
    if target_duration and not any(k in instruction.lower() for k in ["second", "sec", "min"]):
        instruction = f"{instruction}. Target duration: {int(target_duration)} seconds.".strip()

    # 2. Check metadata
    meta = load_metadata(job_id)

    # 3. OBSERVE: Run media understanding analysis if missing
    save_job_state(job_id, JobStatus.ANALYZING)

    # Silence analysis
    silence_file = job_dir / "analysis" / "silence.json"
    if not silence_file.exists() and meta.has_audio:
        try:
            detect_silence(job_id, noise_db=-26.0, min_duration=0.3)
        except Exception as exc:
            logger.warning("Auto-silence detection failed: %s", exc)

    # Scene boundary analysis
    scenes_file = job_dir / "analysis" / "scenes.json"
    if not scenes_file.exists():
        try:
            detect_scenes(job_id)
        except Exception as exc:
            logger.warning("Auto-scene detection failed: %s", exc)

    # Whisper speech-to-text
    transcript_file = job_dir / "analysis" / "transcript.json"
    if not transcript_file.exists() and meta.has_audio:
        try:
            transcribe(job_id, model_size="base", language="en")
        except Exception as exc:
            logger.warning("Auto-transcription failed: %s", exc)

    # 4. UNDERSTAND & PLAN: AI Director EDL formulation
    save_job_state(job_id, JobStatus.PLANNING, instruction=instruction)
    plan_edit(job_id, instruction_override=instruction)

    # 5. ACT: Video execution & rendering with social polish and punch-in
    res = render_video(
        job_id,
        burn_subtitles=burn_subtitles,
        aspect_ratio=aspect_ratio,
        bg_music=bg_music,
        music_volume=music_volume,
        enable_punch_in=enable_punch_in,
    )

    return res


def revise_and_render(
    job_id: uuid.UUID,
    request: RevisionRequest,
) -> RenderResult:
    """
    Iterative feedback loop: revise the existing EDL and re-render without re-transcribing.
    """
    job_dir = JOBS_ROOT / str(job_id)
    if not job_dir.exists():
        raise FileNotFoundError(f"Job '{job_id}' not found.")

    # 1. Revise EDL
    revise_edl(
        job_id,
        revision_instruction=request.revision_instruction,
        target_duration_override=request.target_duration,
    )

    # 2. Resolve render settings (default to existing or new)
    aspect_ratio = request.aspect_ratio or "9:16"
    burn_subtitles = request.burn_subtitles if request.burn_subtitles is not None else True
    enable_punch_in = request.enable_punch_in if request.enable_punch_in is not None else True
    bg_music = request.bg_music if request.bg_music is not None else "chill_ambient"
    music_volume = request.music_volume if request.music_volume is not None else 0.15

    # 3. Render revised edit
    res = render_video(
        job_id,
        burn_subtitles=burn_subtitles,
        aspect_ratio=aspect_ratio,
        bg_music=bg_music,
        music_volume=music_volume,
        enable_punch_in=enable_punch_in,
    )

    return res
