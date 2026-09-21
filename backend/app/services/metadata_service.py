"""
Metadata service — extracts technical video/audio metadata via ffprobe and
persists the result to ``jobs/{job_id}/metadata/media.json``.

ffprobe is invoked through **subprocess** so there is no dependency on the
ffmpeg-python wrapper, only on a working ffprobe binary in PATH (bundled with
any standard FFmpeg installation).
"""

from __future__ import annotations

import json
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.models.media import AudioStreamInfo, MediaMetadata, VideoStreamInfo
from app.services.job_service import JOBS_ROOT
from app.utils.ffmpeg import get_ffprobe_binary as _get_probe_binary


def _run_ffprobe(video_path: Path) -> dict[str, Any]:
    """
    Run ffprobe with JSON output flags on *video_path* and return parsed JSON.

    Raises
    ------
    FileNotFoundError
        When ffprobe binary does not exist.
    ValueError
        When ffprobe exits non-zero (corrupt/unsupported file) or stdout
        cannot be parsed as JSON.
    """
    probe_bin = _get_probe_binary()

    cmd = [
        probe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(video_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Probe binary not executable: '{probe_bin}'. "
            "Install FFmpeg: https://ffmpeg.org/download.html"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"ffprobe timed out after 60 s while probing '{video_path}'."
        ) from exc

    if result.returncode != 0:
        stderr_msg = result.stderr.decode(errors="replace").strip()
        raise ValueError(
            f"ffprobe failed (exit {result.returncode}) for '{video_path}'. "
            f"stderr: {stderr_msg or '(empty)'}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"ffprobe returned invalid JSON for '{video_path}': {exc}"
        ) from exc


def _safe_int(value: Any) -> int | None:
    """Return *value* cast to int, or None if it is missing / non-numeric."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    """Return *value* cast to float, or None if it is missing / non-numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_fps(avg_frame_rate: str) -> float:
    """
    Convert an ffprobe ``avg_frame_rate`` string like ``"30000/1001"`` or
    ``"25/1"`` to a plain float.  Returns 0.0 for empty / invalid strings.
    """
    if not avg_frame_rate or avg_frame_rate in ("0/0", "0"):
        return 0.0
    try:
        return float(Fraction(avg_frame_rate))
    except (ValueError, ZeroDivisionError):
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_metadata(job_id: uuid.UUID) -> MediaMetadata:
    """
    Extract technical metadata from the video stored at
    ``jobs/{job_id}/input/video.mp4`` using ffprobe, build a
    :class:`~app.models.media.MediaMetadata` object, and save it to
    ``jobs/{job_id}/metadata/media.json``.

    Parameters
    ----------
    job_id:
        UUID of the job whose input video should be probed.

    Returns
    -------
    MediaMetadata
        The fully populated metadata model.

    Raises
    ------
    FileNotFoundError
        When the input video does not exist *or* when ffprobe is not in PATH.
    ValueError
        When ffprobe fails (corrupt/unsupported file) or returns invalid JSON.
    """
    video_path = JOBS_ROOT / str(job_id) / "input" / "video.mp4"
    if not video_path.exists():
        raise FileNotFoundError(
            f"Input video not found for job '{job_id}'. "
            "Please upload a video first via POST /upload."
        )

    raw = _run_ffprobe(video_path)

    fmt: dict[str, Any] = raw.get("format", {})
    streams: list[dict[str, Any]] = raw.get("streams", [])

    # ---- parse streams -------------------------------------------------------
    video_streams: list[VideoStreamInfo] = []
    audio_streams: list[AudioStreamInfo] = []

    for stream in streams:
        codec_type = stream.get("codec_type", "")

        if codec_type == "video":
            video_streams.append(
                VideoStreamInfo(
                    codec_name=stream.get("codec_name", "unknown"),
                    codec_long_name=stream.get("codec_long_name", "unknown"),
                    width=int(stream.get("width", 0)),
                    height=int(stream.get("height", 0)),
                    fps=_parse_fps(stream.get("avg_frame_rate", "0/0")),
                    bit_rate=_safe_int(stream.get("bit_rate")),
                    pix_fmt=stream.get("pix_fmt"),
                    duration=_safe_float(stream.get("duration")),
                )
            )
        elif codec_type == "audio":
            audio_streams.append(
                AudioStreamInfo(
                    codec_name=stream.get("codec_name", "unknown"),
                    codec_long_name=stream.get("codec_long_name", "unknown"),
                    sample_rate=_safe_int(stream.get("sample_rate")),
                    channels=_safe_int(stream.get("channels")),
                    channel_layout=stream.get("channel_layout"),
                    bit_rate=_safe_int(stream.get("bit_rate")),
                    duration=_safe_float(stream.get("duration")),
                )
            )

    # ---- build top-level model -----------------------------------------------
    raw_duration = _safe_float(fmt.get("duration"))
    if raw_duration is None:
        # fall back to first video stream duration
        raw_duration = (
            video_streams[0].duration if video_streams and video_streams[0].duration else 0.0
        )

    metadata = MediaMetadata(
        job_id=job_id,
        filename=video_path.name,
        format_name=fmt.get("format_name", "unknown"),
        format_long_name=fmt.get("format_long_name", "unknown"),
        duration=raw_duration,
        size_bytes=_safe_int(fmt.get("size")) or video_path.stat().st_size,
        overall_bit_rate=_safe_int(fmt.get("bit_rate")),
        has_video=len(video_streams) > 0,
        has_audio=len(audio_streams) > 0,
        video_streams=video_streams,
        audio_streams=audio_streams,
    )

    # ---- persist to disk -----------------------------------------------------
    out_path = JOBS_ROOT / str(job_id) / "metadata" / "media.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        metadata.model_dump_json(indent=2),
        encoding="utf-8",
    )

    return metadata


def load_metadata(job_id: uuid.UUID) -> MediaMetadata:
    """
    Load previously extracted metadata from
    ``jobs/{job_id}/metadata/media.json``.

    Parameters
    ----------
    job_id:
        UUID of the job whose metadata should be loaded.

    Returns
    -------
    MediaMetadata
        Deserialised metadata model.

    Raises
    ------
    FileNotFoundError
        When ``media.json`` does not yet exist for this job.
    ValueError
        When the file exists but cannot be parsed as valid JSON / schema.
    """
    meta_path = JOBS_ROOT / str(job_id) / "metadata" / "media.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"Metadata not found for job '{job_id}'. "
            "Run POST /jobs/{job_id}/metadata/extract first."
        )
    try:
        return MediaMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(
            f"Failed to parse metadata file for job '{job_id}': {exc}"
        ) from exc
