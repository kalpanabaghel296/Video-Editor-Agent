"""
Week 3 Test Suite: Video Rendering Engine, Subtitle Re-Timing, and Quality Validation.
"""

import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.editing.captions import format_srt, generate_retimed_subtitles
from app.editing.cuts import build_cuts_filter_graph
from app.main import app
from app.models.edl import EditCut
from app.services.job_service import JOBS_ROOT

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_AUDIO_VIDEO = _REPO_ROOT / "test_videos" / "sample_with_audio.mp4"

client = TestClient(app)


def test_build_cuts_filter_graph():
    """Verify FFmpeg filter graph generation for single and multiple cuts."""
    # 1. Single Cut
    cuts_single = [
        EditCut(clip_id=1, start_time=1.0, end_time=3.5, duration=2.5, reason="Solo clip")
    ]
    fg_s, maps_s = build_cuts_filter_graph(cuts_single, has_audio=True)
    assert "trim=start=1.0:end=3.5" in fg_s
    assert "atrim=start=1.0:end=3.5" in fg_s
    assert maps_s == ["-map", "[outv]", "-map", "[outa]"]

    # 2. Multiple Cuts with Audio
    cuts_multi = [
        EditCut(clip_id=1, start_time=0.5, end_time=1.5, duration=1.0, reason="Clip 1"),
        EditCut(clip_id=2, start_time=2.0, end_time=3.5, duration=1.5, reason="Clip 2"),
    ]
    fg_m, maps_m = build_cuts_filter_graph(cuts_multi, has_audio=True)
    assert "concat=n=2:v=1:a=1[outv][outa]" in fg_m
    assert maps_m == ["-map", "[outv]", "-map", "[outa]"]

    # 3. Multiple Cuts without Audio
    fg_no_a, maps_no_a = build_cuts_filter_graph(cuts_multi, has_audio=False)
    assert "concat=n=2:v=1:a=0[outv]" in fg_no_a
    assert maps_no_a == ["-map", "[outv]"]


def test_retimed_subtitles():
    """Verify subtitle timestamps are accurately shifted to match cut video timeline."""
    raw_segments = [
        {"start": 0.5, "end": 1.4, "text": "Hello world"},
        {"start": 1.6, "end": 2.0, "text": "Silent text to be cut"},
        {"start": 2.2, "end": 3.4, "text": "Welcome to week three"},
    ]
    cuts = [
        EditCut(clip_id=1, start_time=0.5, end_time=1.5, duration=1.0, reason="Clip 1"),
        # 1.5 -> 2.0 is cut out!
        EditCut(clip_id=2, start_time=2.0, end_time=3.5, duration=1.5, reason="Clip 2"),
    ]

    retimed = generate_retimed_subtitles(raw_segments, cuts)
    assert len(retimed) == 2

    # First segment: starts at 0.0s in output
    assert retimed[0]["start"] == 0.0
    assert retimed[0]["text"] == "Hello world"

    # Second segment: starts at accumulated 1.0s + (2.2 - 2.0) = 1.2s in output
    assert retimed[1]["start"] == 1.2
    assert retimed[1]["text"] == "Welcome to week three"

    srt_output = format_srt(retimed)
    assert "00:00:00,000 --> 00:00:00,900" in srt_output
    assert "Hello world" in srt_output
    assert "Welcome to week three" in srt_output


def test_render_and_validation_e2e():
    """End-to-end test of upload -> EDL planning -> rendering -> validation -> export."""
    assert SAMPLE_AUDIO_VIDEO.exists()

    # 1. Upload video
    with open(SAMPLE_AUDIO_VIDEO, "rb") as f:
        resp = client.post(
            "/upload",
            files={"file": ("sample_render.mp4", f, "video/mp4")},
            data={"instruction": "Create a 2 second cut."},
        )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    job_dir = JOBS_ROOT / job_id

    try:
        # 2. Trigger Silence Detection & Planning
        client.post(f"/jobs/{job_id}/silence/detect", params={"noise_db": -30.0, "min_duration": 0.2})
        plan_resp = client.post(f"/jobs/{job_id}/plan")
        assert plan_resp.status_code == 200

        # 3. Trigger Video Rendering
        render_resp = client.post(f"/jobs/{job_id}/render", params={"burn_subtitles": False})
        assert render_resp.status_code == 200
        render_data = render_resp.json()

        assert render_data["status"] == "completed"
        assert render_data["rendered_duration"] > 0
        assert (job_dir / "output" / "final_edit.mp4").is_file()

        # 4. Verify Quality Validation
        assert render_data["validation"]["is_valid"] is True
        assert len(render_data["validation"]["checks"]) >= 4

        val_resp = client.get(f"/jobs/{job_id}/validation")
        assert val_resp.status_code == 200
        assert val_resp.json()["is_valid"] is True

        # 5. Verify Video Export Streaming
        out_vid_resp = client.get(f"/jobs/{job_id}/output/video")
        assert out_vid_resp.status_code == 200
        assert "video/mp4" in out_vid_resp.headers.get("content-type", "")
        assert len(out_vid_resp.content) > 0

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
