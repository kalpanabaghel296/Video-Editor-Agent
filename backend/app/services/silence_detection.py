"""
Silence detection service — finds silent segments in a video's audio track
using ffmpeg's built-in ``silencedetect`` filter.

Output saved to ``jobs/{job_id}/analysis/silence.json``.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from app.services.job_service import JOBS_ROOT
from app.services.metadata_service import load_metadata
from app.utils.ffmpeg import get_ffmpeg_binary


# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------


class SilenceSegment(BaseModel):
    """A contiguous silent period detected in the audio track."""

    silence_id: int = Field(..., description="1-based silence index.")
    start: float = Field(..., description="Silence start time in seconds.")
    end: float = Field(..., description="Silence end time in seconds.")
    duration: float = Field(..., description="Silence duration in seconds.")


class SilenceDetectionResult(BaseModel):
    """Full silence-detection report stored as silence.json."""

    job_id: str = Field(..., description="UUID of the job.")
    noise_tolerance_db: float = Field(
        ..., description="dB threshold used; audio below this is 'silent'."
    )
    min_silence_duration: float = Field(
        ..., description="Minimum silence duration (seconds) to be reported."
    )
    total_silence_segments: int = Field(
        ..., description="Number of silence segments detected."
    )
    total_silence_duration: float = Field(
        ..., description="Total seconds of silence across all segments."
    )
    silences: list[SilenceSegment] = Field(
        default_factory=list, description="Ordered list of silence segments."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Regex patterns for ffmpeg silencedetect log lines
_RE_START = re.compile(r"silence_start:\s*([\d.]+)")
_RE_END   = re.compile(r"silence_end:\s*([\d.]+)")
_RE_DUR   = re.compile(r"silence_duration:\s*([\d.]+)")


def _run_silence_filter(
    video_path: Path,
    noise_db: float,
    min_duration: float,
    ffmpeg_bin: str,
) -> list[SilenceSegment]:
    """
    Run ``ffmpeg -i <video> -af silencedetect=noise=<db>dB:d=<dur> -f null -``
    and parse the stderr output for silence_start / silence_end lines.

    Parameters
    ----------
    video_path : Path
        Full path to the input video.
    noise_db : float
        Audio level (in negative dB) below which audio is considered silent.
        Typical values: -30 to -50.  The sign is applied internally.
    min_duration : float
        Minimum silence length in seconds to report.
    ffmpeg_bin : str
        Path to the ffmpeg binary.

    Returns
    -------
    list[SilenceSegment]
        Ordered list of silence segments.
    """
    noise_arg = f"{noise_db}dB" if noise_db < 0 else f"-{noise_db}dB"
    af_filter = f"silencedetect=noise={noise_arg}:d={min_duration}"

    cmd = [
        ffmpeg_bin,
        "-i", str(video_path),
        "-af", af_filter,
        "-f", "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    # silencedetect writes to stderr regardless of exit code
    log = result.stderr.decode(errors="replace")

    if result.returncode != 0 and "silencedetect" not in log:
        raise RuntimeError(
            f"ffmpeg silencedetect failed (exit {result.returncode}): "
            + log[:500]
        )

    # -- Parse log lines ------------------------------------------------------
    segments: list[SilenceSegment] = []
    starts: list[float] = []
    ends: list[float] = []
    durations: list[float] = []

    for line in log.splitlines():
        m = _RE_START.search(line)
        if m:
            starts.append(float(m.group(1)))
        m = _RE_END.search(line)
        if m:
            ends.append(float(m.group(1)))
        m = _RE_DUR.search(line)
        if m:
            durations.append(float(m.group(1)))

    for idx, (s, e, d) in enumerate(zip(starts, ends, durations), start=1):
        segments.append(
            SilenceSegment(
                silence_id=idx,
                start=round(s, 3),
                end=round(e, 3),
                duration=round(d, 3),
            )
        )

    return segments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_silence(
    job_id: uuid.UUID,
    noise_db: float = -26.0,
    min_duration: float = 0.3,
) -> SilenceDetectionResult:
    """
    Detect silent segments in the audio track of ``jobs/{job_id}/input/video.mp4``.

    Parameters
    ----------
    job_id:
        UUID of the job.
    noise_db:
        Audio level in dB below which audio is considered silent. Default ``-26.0``
        (optimized for mobile and voice recordings with typical ambient room noise).
    min_duration:
        Minimum silence duration in seconds to report. Default ``0.3``.

    Returns
    -------
    SilenceDetectionResult
        Saved to ``jobs/{job_id}/analysis/silence.json``.

    Raises
    ------
    FileNotFoundError
        When the input video or ffmpeg binary is missing.
    RuntimeError
        When ffmpeg fails to process the video.
    """
    video_path = JOBS_ROOT / str(job_id) / "input" / "video.mp4"
    if not video_path.exists():
        raise FileNotFoundError(
            f"Input video not found for job '{job_id}'. Upload a video first."
        )

    # -- Check audio availability --------------------------------------------
    try:
        meta = load_metadata(job_id)
        if not meta.has_audio:
            empty_res = SilenceDetectionResult(
                job_id=str(job_id),
                noise_tolerance_db=noise_db,
                min_silence_duration=min_duration,
                total_silence_segments=0,
                total_silence_duration=0.0,
                silences=[],
            )
            out_path = JOBS_ROOT / str(job_id) / "analysis" / "silence.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(empty_res.model_dump_json(indent=2), encoding="utf-8")
            return empty_res
    except Exception:
        pass

    # -- Resolve ffmpeg binary ------------------------------------------------
    ffmpeg_bin = get_ffmpeg_binary()

    # -- Run detection --------------------------------------------------------
    try:
        segments = _run_silence_filter(video_path, noise_db, min_duration, ffmpeg_bin)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Silence detection failed for job '{job_id}': {exc}"
        ) from exc

    total_silence = round(sum(s.duration for s in segments), 3)

    result = SilenceDetectionResult(
        job_id=str(job_id),
        noise_tolerance_db=noise_db,
        min_silence_duration=min_duration,
        total_silence_segments=len(segments),
        total_silence_duration=total_silence,
        silences=segments,
    )

    # -- Persist --------------------------------------------------------------
    out_path = JOBS_ROOT / str(job_id) / "analysis" / "silence.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    return result


def load_silence(job_id: uuid.UUID) -> SilenceDetectionResult:
    """Load persisted silence data from ``jobs/{job_id}/analysis/silence.json``."""
    path = JOBS_ROOT / str(job_id) / "analysis" / "silence.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Silence data not found for job '{job_id}'. "
            "Run POST /jobs/{job_id}/silence/detect first."
        )
    try:
        return SilenceDetectionResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(
            f"Failed to parse silence.json for job '{job_id}': {exc}"
        ) from exc
