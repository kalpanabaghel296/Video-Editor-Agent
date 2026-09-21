"""
Video rendering engine — executes single-pass FFmpeg cutting, concatenation,
and optional subtitle burning based on the Edit Decision List (EDL).
"""

from __future__ import annotations

import json
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from app.agents.validator import validate_rendered_video
from app.editing.audio import build_ducking_filter, resolve_music_track
from app.editing.captions import generate_retimed_subtitles, save_srt
from app.editing.cuts import build_cuts_filter_graph
from app.models.edl import EditDecisionList
from app.models.job import JobStatus
from app.models.validation import ValidationReport
from app.services.job_service import JOBS_ROOT, save_job_state
from app.services.metadata_service import load_metadata
from app.utils.ffmpeg import get_ffmpeg_binary

logger = logging.getLogger("video_editor.renderer")


class RenderResult(BaseModel):
    """Result summary of a completed video rendering operation."""

    job_id: str = Field(..., description="UUID of the job.")
    status: JobStatus = Field(..., description="Final job status.")
    output_video_url: str = Field(..., description="API stream URL for rendered MP4.")
    subtitles_url: Optional[str] = Field(
        default=None, description="API URL for re-timed .srt subtitles."
    )
    rendered_duration: float = Field(..., description="Duration of output MP4 in seconds.")
    expected_duration: float = Field(..., description="Planned duration from EDL in seconds.")
    file_size_bytes: int = Field(..., description="Size of rendered file in bytes.")
    burned_subtitles: bool = Field(default=False, description="Whether subtitles were burned in.")
    aspect_ratio: str = Field(default="original", description="Aspect ratio rendered.")
    bg_music: Optional[str] = Field(default=None, description="Background music track used.")
    validation: ValidationReport = Field(..., description="Automated quality check report.")


def render_video(
    job_id: uuid.UUID,
    burn_subtitles: bool = False,
    aspect_ratio: str = "original",
    bg_music: Optional[str] = None,
    music_volume: float = 0.15,
    enable_punch_in: bool = False,
) -> RenderResult:
    """
    Render the final video by cutting and concatenating clips defined in ``edl.json``.

    Parameters
    ----------
    job_id : uuid.UUID
        UUID of the job to render.
    burn_subtitles : bool
        If True, burns re-timed subtitles directly into the video stream.
    aspect_ratio : str
        Target aspect ratio: 'original', '9:16' (Shorts/Reels), or '1:1' (Square).
    bg_music : Optional[str]
        Background music track ('chill', 'energetic', or track file path).
    music_volume : float
        Nominal volume level for background music (default 0.15).
    enable_punch_in : bool
        Whether to apply subtle 1.15x camera punch-in zoom on cuts.

    Returns
    -------
    RenderResult
        Comprehensive result including validation report and file endpoints.
    """
    job_dir = JOBS_ROOT / str(job_id)
    input_video = job_dir / "input" / "video.mp4"
    if not input_video.exists():
        raise FileNotFoundError(f"Input video not found for job '{job_id}'.")

    # 1. Load EDL Plan
    edl_path = job_dir / "planning" / "edl.json"
    if not edl_path.exists():
        raise FileNotFoundError(
            f"EDL edit plan not found for job '{job_id}'. Run AI planning first."
        )

    edl = EditDecisionList.model_validate_json(edl_path.read_text(encoding="utf-8"))
    if not edl.cuts:
        raise ValueError("EDL plan contains no cuts to render.")

    # 2. Check input metadata
    meta = load_metadata(job_id)
    has_audio = meta.has_audio
    v_stream = meta.video_streams[0] if meta.video_streams else None
    src_w = v_stream.width if v_stream else None
    src_h = v_stream.height if v_stream else None

    # 3. Setup output directory
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_video = output_dir / "final_edit.mp4"
    output_srt = output_dir / "subtitles.srt"

    # Set status to RENDERING
    save_job_state(job_id, JobStatus.RENDERING)

    # 4. Generate re-timed subtitles if transcript exists
    transcript_path = job_dir / "analysis" / "transcript.json"
    has_subtitles = False
    if transcript_path.exists():
        try:
            t_data = json.loads(transcript_path.read_text(encoding="utf-8"))
            raw_segments = t_data.get("segments", [])
            if raw_segments:
                retimed = generate_retimed_subtitles(raw_segments, edl.cuts)
                if retimed:
                    save_srt(retimed, output_srt)
                    has_subtitles = True
        except Exception as exc:
            logger.warning("Failed to generate subtitles: %s", exc)

    # 5. Build filter graph with aspect ratio reframing and punch-in zoom
    filter_graph, map_args = build_cuts_filter_graph(
        edl.cuts,
        has_audio=has_audio,
        aspect_ratio=aspect_ratio,
        enable_punch_in=enable_punch_in,
        source_width=src_w,
        source_height=src_h,
    )

    # Handle burn_subtitles option
    final_burned = False
    if burn_subtitles and has_subtitles and output_srt.exists():
        # FFmpeg subtitle filter requires escaped path on Windows
        srt_escaped = str(output_srt.resolve()).replace("\\", "/").replace(":", "\\:")
        # Append subtitle burn filter to output video label
        filter_graph += f";[outv]subtitles='{srt_escaped}':force_style='FontSize=16,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,Outline=2'[outvsub]"
        map_args = [("[outvsub]" if m == "[outv]" else m) for m in map_args]
        final_burned = True

    # 6. Handle Background Music & Auto-Ducking
    music_path = resolve_music_track(bg_music)
    extra_inputs: list[str] = []
    used_music_key: Optional[str] = None

    if music_path and music_path.exists():
        extra_inputs = ["-i", str(music_path)]
        used_music_key = music_path.stem
        # Input index 0 is video, input index 1 is music
        duck_filter, final_audio_label = build_ducking_filter(
            speech_label="outa",
            music_input_index=1,
            music_volume=music_volume,
            has_speech=has_audio,
        )
        filter_graph += f";{duck_filter}"
        if has_audio:
            map_args = [final_audio_label if m == "[outa]" else m for m in map_args]
        else:
            map_args.extend(["-map", final_audio_label])
        has_audio = True  # Now has audio stream from background music

    # 7. Assemble and run FFmpeg command
    ffmpeg_bin = get_ffmpeg_binary()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_video),
        *extra_inputs,
        "-filter_complex", filter_graph,
        *map_args,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
    ]

    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])

    cmd.append(str(output_video))

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
    except Exception as exc:
        save_job_state(job_id, JobStatus.FAILED)
        raise RuntimeError(f"FFmpeg execution failed: {exc}") from exc

    if proc.returncode != 0:
        err_log = proc.stderr.decode(errors="replace")
        # If burning subtitles failed (e.g. libass missing), retry without subtitle burn
        if final_burned:
            logger.warning("Burn-in failed, retrying without burned subtitles...")
            filter_graph, map_args = build_cuts_filter_graph(
                edl.cuts,
                has_audio=meta.has_audio,
                aspect_ratio=aspect_ratio,
                enable_punch_in=enable_punch_in,
                source_width=src_w,
                source_height=src_h,
            )
            if music_path and music_path.exists():
                duck_filter, final_audio_label = build_ducking_filter(
                    speech_label="outa",
                    music_input_index=1,
                    music_volume=music_volume,
                    has_speech=meta.has_audio,
                )
                filter_graph += f";{duck_filter}"
                if meta.has_audio:
                    map_args = [final_audio_label if m == "[outa]" else m for m in map_args]
                else:
                    map_args.extend(["-map", final_audio_label])

            retry_cmd = [
                ffmpeg_bin,
                "-y",
                "-i", str(input_video),
                *extra_inputs,
                "-filter_complex", filter_graph,
                *map_args,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
            ]
            if has_audio:
                retry_cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            retry_cmd.append(str(output_video))

            proc = subprocess.run(
                retry_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
                check=False,
            )
            final_burned = False

        if proc.returncode != 0:
            save_job_state(job_id, JobStatus.FAILED)
            err_snippet = proc.stderr.decode(errors="replace")[-500:]
            raise RuntimeError(f"FFmpeg rendering failed (exit {proc.returncode}): {err_snippet}")

    # 7. Quality Validation
    save_job_state(job_id, JobStatus.VALIDATING)
    val_report = validate_rendered_video(job_id)

    # 8. Mark COMPLETED
    save_job_state(job_id, JobStatus.COMPLETED)

    # 9. Return Result
    return RenderResult(
        job_id=str(job_id),
        status=JobStatus.COMPLETED,
        output_video_url=f"/jobs/{job_id}/output/video",
        subtitles_url=f"/jobs/{job_id}/output/subtitles" if has_subtitles else None,
        rendered_duration=val_report.rendered_duration,
        expected_duration=val_report.expected_duration,
        file_size_bytes=val_report.file_size_bytes,
        burned_subtitles=final_burned,
        aspect_ratio=aspect_ratio,
        bg_music=used_music_key,
        validation=val_report,
    )
