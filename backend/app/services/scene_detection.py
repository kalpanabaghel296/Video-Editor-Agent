"""
Scene detection service — detects shot boundaries in an uploaded video using
PySceneDetect and persists results to ``jobs/{job_id}/analysis/scenes.json``.

Two detectors are supported (selectable via the ``detector`` parameter):
- ``"content"``  → ContentDetector  (HSV histogram diff, best for most footage)
- ``"threshold"``→ ThresholdDetector (luminance threshold, good for fade-cuts)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from app.models.scene import SceneDetectionResult, SceneInfo
from app.services.job_service import JOBS_ROOT


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _seconds_to_timecode(seconds: float) -> str:
    """Convert *seconds* (float) to ``HH:MM:SS.mmm`` timecode string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _get_ffmpeg_path() -> str | None:
    """
    Return the path to an ffmpeg binary for OpenCV-based backends, or None
    if imageio-ffmpeg is not installed (OpenCV can still open many formats
    without an explicit ffmpeg path).
    """
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DetectorType = Literal["content", "threshold"]


def detect_scenes(
    job_id: uuid.UUID,
    detector: DetectorType = "content",
    threshold: float | None = None,
    min_scene_len_frames: int = 15,
) -> SceneDetectionResult:
    """
    Run PySceneDetect on ``jobs/{job_id}/input/video.mp4`` and write the
    result to ``jobs/{job_id}/analysis/scenes.json``.

    Parameters
    ----------
    job_id:
        UUID of the job whose video should be analysed.
    detector:
        ``"content"`` (default) uses :class:`scenedetect.ContentDetector` —
        compares HSV frame histograms. ``"threshold"`` uses
        :class:`scenedetect.ThresholdDetector` — detects luminance cuts.
    threshold:
        Detection sensitivity. Defaults to ``27.0`` for content, ``12.0`` for
        threshold detector. Lower = more sensitive (more scenes found).
    min_scene_len_frames:
        Minimum number of frames a scene must span to be kept.
        Helps filter flash-cuts and encoding artefacts (default: 15).

    Returns
    -------
    SceneDetectionResult
        Structured detection report (also saved to ``scenes.json``).

    Raises
    ------
    FileNotFoundError
        When the input video does not exist.
    RuntimeError
        When PySceneDetect fails to open or process the video.
    """
    # ── Validate input ────────────────────────────────────────────────────
    video_path = JOBS_ROOT / str(job_id) / "input" / "video.mp4"
    if not video_path.exists():
        raise FileNotFoundError(
            f"Input video not found for job '{job_id}'. "
            "Please upload a video first via POST /upload."
        )

    # ── Import PySceneDetect (lazy — keeps startup fast) ─────────────────
    try:
        from scenedetect import (  # type: ignore
            SceneManager,
            open_video,
        )
        from scenedetect.detectors import (  # type: ignore
            ContentDetector,
            ThresholdDetector,
        )
    except ImportError as exc:
        raise RuntimeError(
            "PySceneDetect is not installed. "
            "Run: pip install scenedetect[opencv]"
        ) from exc

    # ── Build detector ────────────────────────────────────────────────────
    if detector == "content":
        effective_threshold = threshold if threshold is not None else 27.0
        det = ContentDetector(
            threshold=effective_threshold,
            min_scene_len=min_scene_len_frames,
        )
        detector_label = f"ContentDetector(threshold={effective_threshold})"
    else:
        effective_threshold = threshold if threshold is not None else 12.0
        det = ThresholdDetector(
            threshold=effective_threshold,
            min_scene_len=min_scene_len_frames,
        )
        detector_label = f"ThresholdDetector(threshold={effective_threshold})"

    # ── Open video & detect ───────────────────────────────────────────────
    try:
        video = open_video(str(video_path))
    except Exception as exc:
        raise RuntimeError(
            f"PySceneDetect could not open '{video_path}': {exc}. "
            "Ensure the video file is valid and OpenCV is installed."
        ) from exc

    scene_manager = SceneManager()
    scene_manager.add_detector(det)

    try:
        scene_manager.detect_scenes(video, show_progress=False)
    except Exception as exc:
        raise RuntimeError(
            f"Scene detection failed for job '{job_id}': {exc}"
        ) from exc

    raw_scenes = scene_manager.get_scene_list()

    # ── Build structured result ───────────────────────────────────────────
    scenes: list[SceneInfo] = []
    for idx, (start_tc, end_tc) in enumerate(raw_scenes, start=1):
        start_sec = start_tc.get_seconds()
        end_sec = end_tc.get_seconds()
        scenes.append(
            SceneInfo(
                scene_number=idx,
                start_time=round(start_sec, 3),
                end_time=round(end_sec, 3),
                duration=round(end_sec - start_sec, 3),
                start_timecode=_seconds_to_timecode(start_sec),
                end_timecode=_seconds_to_timecode(end_sec),
            )
        )

    # If no cut transitions were found, the entire recording is 1 continuous scene
    if not scenes:
        try:
            from app.services.metadata_service import load_metadata
            meta = load_metadata(job_id)
            total_dur = round(meta.duration, 3)
        except Exception:
            total_dur = 0.0

        if total_dur > 0:
            scenes.append(
                SceneInfo(
                    scene_number=1,
                    start_time=0.0,
                    end_time=total_dur,
                    duration=total_dur,
                    start_timecode="00:00:00.000",
                    end_timecode=_seconds_to_timecode(total_dur),
                )
            )

    result = SceneDetectionResult(
        job_id=str(job_id),
        total_scenes=len(scenes),
        detector=detector_label,
        scenes=scenes,
    )

    # ── Persist to disk ───────────────────────────────────────────────────
    out_path = JOBS_ROOT / str(job_id) / "analysis" / "scenes.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    return result


def load_scenes(job_id: uuid.UUID) -> SceneDetectionResult:
    """
    Load previously detected scenes from
    ``jobs/{job_id}/analysis/scenes.json``.

    Raises
    ------
    FileNotFoundError
        When ``scenes.json`` has not been generated for this job.
    ValueError
        When the file is present but cannot be parsed.
    """
    scenes_path = JOBS_ROOT / str(job_id) / "analysis" / "scenes.json"
    if not scenes_path.exists():
        raise FileNotFoundError(
            f"Scene data not found for job '{job_id}'. "
            "Run POST /jobs/{job_id}/scenes/detect first."
        )
    try:
        return SceneDetectionResult.model_validate_json(
            scenes_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ValueError(
            f"Failed to parse scenes.json for job '{job_id}': {exc}"
        ) from exc
