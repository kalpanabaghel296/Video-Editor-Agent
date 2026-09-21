"""
Transcription service — extracts audio from a video and transcribes it using
OpenAI Whisper (local model, no API key required).

Output saved to ``jobs/{job_id}/analysis/transcript.json``.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from app.models.transcript import TranscriptSegment, TranscriptionResult
from app.services.job_service import JOBS_ROOT
from app.services.metadata_service import load_metadata
from app.utils.ffmpeg import get_ffmpeg_binary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_audio(video_path: Path, audio_out: Path) -> None:
    """
    Extract the first audio stream from *video_path* to a 16-kHz mono WAV
    file at *audio_out* using FFmpeg.

    Raises
    ------
    RuntimeError
        If ffmpeg fails or is not found.
    """
    ffmpeg_bin = get_ffmpeg_binary()

    cmd = [
        ffmpeg_bin,
        "-y",                   # overwrite output
        "-i", str(video_path),  # input
        "-vn",                  # no video
        "-acodec", "pcm_s16le", # 16-bit PCM
        "-ar", "16000",         # 16 kHz sample rate (Whisper requirement)
        "-ac", "1",             # mono
        str(audio_out),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg audio extraction failed: {stderr}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def transcribe(
    job_id: uuid.UUID,
    model_size: str = "tiny",
    language: Optional[str] = None,
) -> TranscriptionResult:
    """
    Transcribe the audio from ``jobs/{job_id}/input/video.mp4`` using Whisper.

    Parameters
    ----------
    job_id:
        UUID of the job.
    model_size:
        Whisper model: ``"tiny"``, ``"base"``, ``"small"``, ``"medium"``, ``"large"``.
        Defaults to ``"tiny"`` for fast local processing.
    language:
        Optional ISO-639-1 language code (e.g. ``"en"``). If None, Whisper
        auto-detects the language.

    Returns
    -------
    TranscriptionResult
        Structured transcript saved to ``jobs/{job_id}/analysis/transcript.json``.
    """
    video_path = JOBS_ROOT / str(job_id) / "input" / "video.mp4"
    if not video_path.exists():
        raise FileNotFoundError(
            f"Input video not found for job '{job_id}'. Upload a video first."
        )

    # Check if video has an audio stream from metadata
    try:
        meta = load_metadata(job_id)
        if not meta.has_audio:
            empty_result = TranscriptionResult(
                job_id=str(job_id),
                language=None,
                full_text="",
                segments=[],
                model_used=model_size,
            )
            out_path = JOBS_ROOT / str(job_id) / "analysis" / "transcript.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(empty_result.model_dump_json(indent=2), encoding="utf-8")
            return empty_result
    except Exception:
        pass

    # -- Import Whisper (lazy) ------------------------------------------------
    try:
        import whisper  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "openai-whisper is not installed. Run: pip install openai-whisper"
        ) from exc

    # -- Extract audio to a temp WAV file ------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = Path(tmp) / "audio.wav"
        try:
            _extract_audio(video_path, audio_path)
        except RuntimeError as exc:
            raise RuntimeError(f"Audio extraction failed for job '{job_id}': {exc}") from exc

        # -- Transcribe -------------------------------------------------------
        try:
            model = whisper.load_model(model_size, device="cpu")
            options: dict = {
                "fp16": False,
                "condition_on_previous_text": False,
                "no_speech_threshold": 0.6,
                "compression_ratio_threshold": 2.4,
                "logprob_threshold": -1.0,
            }
            if language:
                options["language"] = language
                if language == "en":
                    options["initial_prompt"] = "Clear conversational speech in English."
                elif language == "hi":
                    options["initial_prompt"] = "स्पष्ट हिंदी वार्तालाप।"

            raw = model.transcribe(str(audio_path), **options)
        except Exception as exc:
            raise RuntimeError(
                f"Whisper transcription failed for job '{job_id}': {exc}"
            ) from exc

    # -- Build result with hallucination filtering ----------------------------
    detected_lang = raw.get("language", language)
    cleaned_segments: list[TranscriptSegment] = []
    seg_idx = 1

    for seg in raw.get("segments", []):
        raw_text = seg.get("text", "").strip()

        # If language is English, remove accidental CJK / Chinese hallucinated tokens
        if language == "en" or detected_lang == "en":
            import re
            cleaned_text = re.sub(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", "", raw_text).strip()
            cleaned_text = re.sub(r"\s+", " ", cleaned_text)
        else:
            cleaned_text = raw_text

        # Only retain non-empty meaningful speech segments
        if cleaned_text and len(cleaned_text) >= 2:
            cleaned_segments.append(
                TranscriptSegment(
                    segment_id=seg_idx,
                    start=round(seg["start"], 3),
                    end=round(seg["end"], 3),
                    text=cleaned_text,
                )
            )
            seg_idx += 1

    full_clean_text = " ".join(s.text for s in cleaned_segments).strip()

    result = TranscriptionResult(
        job_id=str(job_id),
        language=detected_lang,
        full_text=full_clean_text,
        segments=cleaned_segments,
        model_used=model_size,
    )

    # -- Persist --------------------------------------------------------------
    out_path = JOBS_ROOT / str(job_id) / "analysis" / "transcript.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    return result


def load_transcript(job_id: uuid.UUID) -> TranscriptionResult:
    """Load persisted transcript from ``jobs/{job_id}/analysis/transcript.json``."""
    path = JOBS_ROOT / str(job_id) / "analysis" / "transcript.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Transcript not found for job '{job_id}'. "
            "Run POST /jobs/{job_id}/transcribe first."
        )
    try:
        return TranscriptionResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse transcript.json for job '{job_id}': {exc}") from exc
