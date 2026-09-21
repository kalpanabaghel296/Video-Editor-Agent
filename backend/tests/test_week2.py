"""
Week 2 Test Suite: Transcription, Silence Detection, AI Edit Planning, and Video Streaming.
"""

import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.job_service import JOBS_ROOT

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_AUDIO_VIDEO = _REPO_ROOT / "test_videos" / "sample_with_audio.mp4"
SAMPLE_NO_AUDIO_VIDEO = _REPO_ROOT / "test_videos" / "sample_no_audio.mp4"

client = TestClient(app)


def test_week2_pipeline_with_audio():
    """Verify transcription, silence detection, edit planning, and video streaming on video with audio."""
    assert SAMPLE_AUDIO_VIDEO.exists()

    # 1. Upload video
    with open(SAMPLE_AUDIO_VIDEO, "rb") as f:
        resp = client.post(
            "/upload",
            files={"file": ("sample.mp4", f, "video/mp4")},
            data={"instruction": "Create a 2 second energetic short cut."},
        )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    job_dir = JOBS_ROOT / job_id

    try:
        # 2. Test Video Streaming Endpoint
        video_resp = client.get(f"/jobs/{job_id}/video")
        assert video_resp.status_code == 200
        assert "video/mp4" in video_resp.headers.get("content-type", "")
        assert len(video_resp.content) > 0

        # 3. Test Silence Detection
        silence_resp = client.post(f"/jobs/{job_id}/silence/detect", params={"noise_db": -30.0, "min_duration": 0.3})
        assert silence_resp.status_code == 200
        silence_data = silence_resp.json()
        assert "silences" in silence_data
        assert (job_dir / "analysis" / "silence.json").is_file()

        # 4. Test Transcription
        trans_resp = client.post(f"/jobs/{job_id}/transcribe", params={"model_size": "tiny"})
        assert trans_resp.status_code == 200
        trans_data = trans_resp.json()
        assert "segments" in trans_data
        assert (job_dir / "analysis" / "transcript.json").is_file()

        # 5. Test AI Director Planning
        plan_resp = client.post(f"/jobs/{job_id}/plan")
        assert plan_resp.status_code == 200
        plan_data = plan_resp.json()
        assert len(plan_data["cuts"]) > 0
        assert plan_data["total_planned_duration"] > 0
        assert (job_dir / "planning" / "edl.json").is_file()
        assert "AI Director formulated" in plan_data["summary"]

        # 6. Test GET endpoints
        get_plan = client.get(f"/jobs/{job_id}/plan")
        assert get_plan.status_code == 200
        assert get_plan.json()["summary"] == plan_data["summary"]

        get_silence = client.get(f"/jobs/{job_id}/silence")
        assert get_silence.status_code == 200

        get_transcript = client.get(f"/jobs/{job_id}/transcript")
        assert get_transcript.status_code == 200

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)


def test_week2_pipeline_no_audio_graceful():
    """Verify transcription, silence detection, and edit planning succeed cleanly on videos without audio."""
    assert SAMPLE_NO_AUDIO_VIDEO.exists()

    with open(SAMPLE_NO_AUDIO_VIDEO, "rb") as f:
        resp = client.post(
            "/upload",
            files={"file": ("no_audio.mp4", f, "video/mp4")},
        )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    job_dir = JOBS_ROOT / job_id

    try:
        # Transcription on no-audio returns empty segments without crash
        trans_resp = client.post(f"/jobs/{job_id}/transcribe")
        assert trans_resp.status_code == 200
        assert trans_resp.json()["segments"] == []

        # Silence detection on no-audio returns 0 segments without crash
        silence_resp = client.post(f"/jobs/{job_id}/silence/detect")
        assert silence_resp.status_code == 200
        assert silence_resp.json()["total_silence_segments"] == 0

        # AI Director still formulates valid cuts for visual footage
        plan_resp = client.post(f"/jobs/{job_id}/plan")
        assert plan_resp.status_code == 200
        assert len(plan_resp.json()["cuts"]) > 0

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main(["-v", __file__])
