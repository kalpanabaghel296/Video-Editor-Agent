"""
Live End-to-End Test for Week 1 AI Video Editing Agent.

Verifies live communication across:
- React frontend server on http://localhost:5173
- FastAPI backend server on http://127.0.0.1:8000
- Full upload -> FFprobe metadata extraction -> state persistence -> status query flow
"""

import json
import urllib.request
import urllib.error
from pathlib import Path
import requests

API_BASE = "http://127.0.0.1:8000"
FRONTEND_BASE = "http://localhost:5173"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_VIDEO = REPO_ROOT / "test_videos" / "sample_with_audio.mp4"


def test_live_frontend():
    print("[1/5] Testing Frontend Server availability...")
    res = requests.get(FRONTEND_BASE, timeout=5)
    assert res.status_code == 200, f"Frontend returned {res.status_code}"
    assert "id=\"root\"" in res.text, "Root element missing from HTML"
    assert "/src/main.jsx" in res.text, "main.jsx script reference missing"
    print("  [OK] Frontend is serving HTML properly.")


def test_live_health():
    print("[2/5] Testing Backend /health endpoint...")
    res = requests.get(f"{API_BASE}/health", timeout=5)
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    print("  [OK] Backend health check passed.")


def test_live_upload_and_metadata():
    print("[3/5] Testing Live Video Upload and Automatic Metadata Extraction...")
    assert SAMPLE_VIDEO.exists(), f"Sample video not found at {SAMPLE_VIDEO}"

    instruction = (
        "Create a 30 second energetic short-form video. "
        "Remove unnecessary silence and keep the most important spoken parts."
    )

    with open(SAMPLE_VIDEO, "rb") as f:
        files = {"file": ("sample_with_audio.mp4", f, "video/mp4")}
        data = {"instruction": instruction}
        res = requests.post(f"{API_BASE}/upload", files=files, data=data, timeout=30)

    assert res.status_code == 201, f"Upload returned {res.status_code}: {res.text}"
    resp_json = res.json()
    job_id = resp_json.get("job_id")
    assert job_id, "No job_id returned"
    print(f"  [OK] Job created successfully with ID: {job_id}")

    # Verify status
    assert resp_json.get("status") == "pending"
    print(f"  [OK] Job status: {resp_json['status']}")

    # Verify metadata
    meta = resp_json.get("metadata")
    assert meta is not None, "Metadata was not returned"
    assert meta["has_video"] is True, "Expected has_video=True"
    assert meta["has_audio"] is True, "Expected has_audio=True"
    assert meta["duration"] > 0, "Expected duration > 0"
    assert len(meta["video_streams"]) > 0, "Expected at least 1 video stream"
    assert len(meta["audio_streams"]) > 0, "Expected at least 1 audio stream"

    v_stream = meta["video_streams"][0]
    a_stream = meta["audio_streams"][0]
    print(f"  [OK] Extracted Video Codec: {v_stream['codec_name']}")
    print(f"  [OK] Extracted Resolution: {v_stream['width']}x{v_stream['height']}")
    print(f"  [OK] Extracted FPS: {v_stream['fps']}")
    print(f"  [OK] Extracted Audio Codec: {a_stream['codec_name']}")
    print(f"  [OK] Extracted Duration: {meta['duration']}s")

    # Verify persisted files on disk
    job_dir = REPO_ROOT / "jobs" / job_id
    media_json = job_dir / "metadata" / "media.json"
    state_json = job_dir / "state.json"
    instruction_txt = job_dir / "instruction.txt"

    assert media_json.exists(), f"media.json missing at {media_json}"
    assert state_json.exists(), f"state.json missing at {state_json}"
    assert instruction_txt.exists(), f"instruction.txt missing at {instruction_txt}"
    print("  [OK] media.json, state.json, and instruction.txt persisted on disk.")

    # Verify GET /jobs/{job_id}
    print("[4/5] Testing GET /jobs/{job_id}...")
    st_res = requests.get(f"{API_BASE}/jobs/{job_id}", timeout=5)
    assert st_res.status_code == 200
    st_data = st_res.json()
    assert st_data["job_id"] == job_id
    assert st_data["status"] == "pending"
    print(f"  [OK] GET /jobs/{job_id} returned status: {st_data['status']}")


def test_live_job_not_found():
    print("[5/5] Testing 404 for nonexistent job ID...")
    res = requests.get(f"{API_BASE}/jobs/00000000-0000-0000-0000-000000000000", timeout=5)
    assert res.status_code == 404
    print("  [OK] Unknown job correctly returned 404 Not Found.")


if __name__ == "__main__":
    print("========================================")
    print("RUNNING LIVE END-TO-END WEEK 1 TEST SUITE")
    print("========================================")
    test_live_frontend()
    test_live_health()
    job_id = test_live_upload_and_metadata()
    test_live_job_status(job_id)
    test_live_job_not_found()
    print("========================================")
    print("ALL WEEK 1 END-TO-END TESTS PASSED SUCCESSFULLY! [OK]")
    print("========================================")
