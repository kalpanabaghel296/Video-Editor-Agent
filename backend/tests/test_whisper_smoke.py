"""
Whisper Smoke Test for Week 1.

Verifies:
1. openai-whisper library can be imported.
2. Audio preprocessing (load, pad/trim, log-mel spectrogram) works.
3. Whisper tiny model can load on CPU and process audio without error.
"""

import os
from pathlib import Path
import pytest
import numpy as np

# Resolve sample audio/video path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_VIDEO = _REPO_ROOT / "test_videos" / "sample_with_audio.mp4"


def test_whisper_import():
    """Verify openai-whisper imports successfully."""
    import whisper
    assert hasattr(whisper, "load_model")
    assert hasattr(whisper, "load_audio")


def test_whisper_audio_preprocessing():
    """Verify Whisper can process audio into mel spectrogram."""
    import whisper

    if not SAMPLE_VIDEO.exists():
        pytest.skip(f"Sample video not found at {SAMPLE_VIDEO}")

    audio = whisper.load_audio(str(SAMPLE_VIDEO))
    assert isinstance(audio, np.ndarray)
    assert len(audio) > 0

    # Test pad_or_trim
    trimmed = whisper.pad_or_trim(audio)
    assert len(trimmed) == whisper.audio.N_SAMPLES

    # Test mel spectrogram
    mel = whisper.log_mel_spectrogram(trimmed)
    assert mel.shape[0] == 80  # n_mels


def test_whisper_model_smoke():
    """Verify Whisper tiny model loads and processes audio on CPU."""
    import whisper

    if not SAMPLE_VIDEO.exists():
        pytest.skip(f"Sample video not found at {SAMPLE_VIDEO}")

    model = whisper.load_model("tiny", device="cpu")
    result = model.transcribe(str(SAMPLE_VIDEO), fp16=False)
    assert "text" in result
    print("\n[Whisper Smoke Test] Transcribed text:", repr(result["text"]))


if __name__ == "__main__":
    print("Running Whisper smoke tests directly...")
    test_whisper_import()
    print("[OK] Whisper import passed.")
    test_whisper_audio_preprocessing()
    print("[OK] Whisper audio preprocessing passed.")
    test_whisper_model_smoke()
    print("[OK] Whisper tiny model transcription passed.")
    print("All Whisper smoke tests passed!")
