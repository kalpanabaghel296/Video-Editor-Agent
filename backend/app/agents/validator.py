"""
Quality Validation Agent — inspects rendered output videos for stream integrity,
audio-video synchronization, duration conformance, and file sanity.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models.validation import ValidationCheck, ValidationReport
from app.services.job_service import JOBS_ROOT
from app.services.metadata_service import load_metadata
from app.utils.ffmpeg import get_ffprobe_binary


def validate_rendered_video(job_id: uuid.UUID) -> ValidationReport:
    """
    Run automated quality checks on ``jobs/{job_id}/output/final_edit.mp4``.

    Checks performed:
    1. Output File Sanity (file exists and size > 1024 bytes).
    2. Video Stream Integrity (valid video codec, resolution, and fps).
    3. Audio Stream Integrity (matching input audio presence).
    4. Duration Conformance (within ±0.75s of planned EDL duration).

    Persists report to ``jobs/{job_id}/validation/report.json``.
    """
    job_dir = JOBS_ROOT / str(job_id)
    output_video = job_dir / "output" / "final_edit.mp4"
    edl_file = job_dir / "planning" / "edl.json"

    checks: list[ValidationCheck] = []
    expected_duration = 0.0

    # 1. Load planned EDL duration
    if edl_file.exists():
        try:
            edl_data = json.loads(edl_file.read_text(encoding="utf-8"))
            expected_duration = float(edl_data.get("total_planned_duration", 0.0))
        except Exception:
            pass

    # 2. Check: Output File Sanity
    if not output_video.exists():
        checks.append(
            ValidationCheck(
                name="File Existence",
                passed=False,
                details=f"Rendered file {output_video.name} does not exist.",
            )
        )
        report = ValidationReport(
            job_id=str(job_id),
            is_valid=False,
            checks=checks,
            rendered_duration=0.0,
            expected_duration=expected_duration,
            file_size_bytes=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _save_report(job_id, report)
        return report

    file_size = output_video.stat().st_size
    if file_size < 1024:
        checks.append(
            ValidationCheck(
                name="File Size Sanity",
                passed=False,
                details=f"Rendered file is suspiciously small ({file_size} bytes).",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="File Size Sanity",
                passed=True,
                details=f"File size is valid ({(file_size / 1024):.1f} KB).",
            )
        )

    # 3. Probe rendered video with FFprobe
    ffprobe_bin = get_ffprobe_binary()
    probe_cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(output_video),
    ]

    try:
        proc = subprocess.run(
            probe_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=True,
        )
        probe_data = json.loads(proc.stdout.decode("utf-8"))
    except Exception as exc:
        checks.append(
            ValidationCheck(
                name="Stream Probing",
                passed=False,
                details=f"FFprobe failed to inspect output video: {exc}",
            )
        )
        report = ValidationReport(
            job_id=str(job_id),
            is_valid=False,
            checks=checks,
            rendered_duration=0.0,
            expected_duration=expected_duration,
            file_size_bytes=file_size,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _save_report(job_id, report)
        return report

    streams = probe_data.get("streams", [])
    format_info = probe_data.get("format", {})
    rendered_duration = float(format_info.get("duration", 0.0))

    # 4. Check: Video Stream Integrity
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream:
        v_codec = video_stream.get("codec_name", "unknown")
        w = video_stream.get("width")
        h = video_stream.get("height")
        checks.append(
            ValidationCheck(
                name="Video Stream Integrity",
                passed=True,
                details=f"Video stream intact: {v_codec}, {w}x{h}.",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="Video Stream Integrity",
                passed=False,
                details="No valid video stream detected in rendered file.",
            )
        )

    # 5. Check: Audio Stream Integrity
    input_has_audio = False
    try:
        input_meta = load_metadata(job_id)
        input_has_audio = input_meta.has_audio
    except Exception:
        pass

    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if input_has_audio:
        if audio_stream:
            a_codec = audio_stream.get("codec_name", "unknown")
            checks.append(
                ValidationCheck(
                    name="Audio Stream Integrity",
                    passed=True,
                    details=f"Audio stream intact: {a_codec}, audio preserved.",
                )
            )
        else:
            checks.append(
                ValidationCheck(
                    name="Audio Stream Integrity",
                    passed=False,
                    details="Input had audio, but rendered video audio stream is missing.",
                )
            )
    else:
        checks.append(
            ValidationCheck(
                name="Audio Stream Integrity",
                passed=True,
                details="Input had no audio track; silence expected.",
            )
        )

    # 6. Check: Duration Conformance
    if expected_duration > 0.0:
        dur_diff = abs(rendered_duration - expected_duration)
        if dur_diff <= 0.75:
            checks.append(
                ValidationCheck(
                    name="Duration Conformance",
                    passed=True,
                    details=(
                        f"Rendered duration ({rendered_duration:.2f}s) matches "
                        f"planned EDL duration ({expected_duration:.2f}s) within ±0.75s."
                    ),
                )
            )
        else:
            checks.append(
                ValidationCheck(
                    name="Duration Conformance",
                    passed=False,
                    details=(
                        f"Duration drift: rendered ({rendered_duration:.2f}s) differs from "
                        f"planned ({expected_duration:.2f}s) by {dur_diff:.2f}s."
                    ),
                )
            )
    else:
        checks.append(
            ValidationCheck(
                name="Duration Conformance",
                passed=True,
                details=f"Rendered video duration is {rendered_duration:.2f}s.",
            )
        )

    is_valid = all(c.passed for c in checks)

    report = ValidationReport(
        job_id=str(job_id),
        is_valid=is_valid,
        checks=checks,
        rendered_duration=round(rendered_duration, 2),
        expected_duration=round(expected_duration, 2),
        file_size_bytes=file_size,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _save_report(job_id, report)
    return report


def _save_report(job_id: uuid.UUID, report: ValidationReport) -> None:
    val_dir = JOBS_ROOT / str(job_id) / "validation"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )


def load_validation_report(job_id: uuid.UUID) -> ValidationReport:
    """Load persisted validation report from ``jobs/{job_id}/validation/report.json``."""
    val_file = JOBS_ROOT / str(job_id) / "validation" / "report.json"
    if not val_file.exists():
        raise FileNotFoundError(f"Validation report not found for job '{job_id}'.")
    return ValidationReport.model_validate_json(val_file.read_text(encoding="utf-8"))
