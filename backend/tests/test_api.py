"""
Backend API Tests for Week 1.

Tests:
1. GET /health
2. POST /upload with video and optional instruction
3. Metadata auto-extraction on upload
4. GET /jobs/{job_id} status endpoint
5. GET /jobs/{unknown_id} returns 404
6. POST /jobs/{job_id}/metadata/extract manual extraction
7. GET /jobs/{job_id}/metadata
"""

import shutil
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.job_service import JOBS_ROOT

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_VIDEO_PATH = _REPO_ROOT / "test_videos" / "sample_with_audio.mp4"

client = TestClient(app)


def test_health_endpoint():
    """Verify /health returns status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_and_auto_metadata():
    """Verify POST /upload saves video, extracts metadata, and saves state."""
    assert SAMPLE_VIDEO_PATH.exists(), f"Missing sample video: {SAMPLE_VIDEO_PATH}"

    with open(SAMPLE_VIDEO_PATH, "rb") as f:
        response = client.post(
            "/upload",
            files={"file": ("sample_with_audio.mp4", f, "video/mp4")},
            data={"instruction": "Create an energetic short-form edit."},
        )

    assert response.status_code == 201
    data = response.json()
    job_id = data["job_id"]
    assert job_id is not None
    assert data["status"] == "pending"
    assert data["instruction"] == "Create an energetic short-form edit."

    # Check metadata returned in response
    metadata = data.get("metadata")
    assert metadata is not None
    assert metadata["has_video"] is True
    assert metadata["has_audio"] is True
    assert metadata["duration"] > 0
    assert len(metadata["video_streams"]) > 0
    assert metadata["video_streams"][0]["codec_name"] == "h264"
    assert metadata["video_streams"][0]["width"] == 640
    assert metadata["video_streams"][0]["height"] == 360
    assert len(metadata["audio_streams"]) > 0
    assert metadata["audio_streams"][0]["codec_name"] == "aac"

    # Verify persisted files on disk
    job_dir = JOBS_ROOT / job_id
    assert (job_dir / "input" / "video.mp4").is_file()
    assert (job_dir / "metadata" / "media.json").is_file()
    assert (job_dir / "state.json").is_file()
    assert (job_dir / "instruction.txt").is_file()
    assert (job_dir / "instruction.txt").read_text(encoding="utf-8") == "Create an energetic short-form edit."

    # Test GET /jobs/{job_id}
    status_resp = client.get(f"/jobs/{job_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["job_id"] == job_id
    assert status_data["status"] == "pending"

    # Test GET /jobs/{job_id}/metadata
    meta_resp = client.get(f"/jobs/{job_id}/metadata")
    assert meta_resp.status_code == 200
    meta_data = meta_resp.json()
    assert meta_data["duration"] == metadata["duration"]

    # Test manual POST /jobs/{job_id}/metadata/extract
    re_extract_resp = client.post(f"/jobs/{job_id}/metadata/extract")
    assert re_extract_resp.status_code == 200
    assert re_extract_resp.json()["duration"] == metadata["duration"]

    # Clean up test job directory
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def test_job_status_not_found():
    """Verify GET /jobs/{unknown_id} returns 404."""
    random_id = uuid.uuid4()
    response = client.get(f"/jobs/{random_id}")
    assert response.status_code == 404
    assert f"Job '{random_id}' not found." in response.json()["detail"]


if __name__ == "__main__":
    pytest.main(["-v", __file__])
