"""
Week 4 Test Suite: Social Re-Framing (9:16 / 1:1), Background Music, and Audio Ducking.
"""

import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.editing.audio import build_ducking_filter, get_available_music_tracks, resolve_music_track
from app.editing.cuts import build_cuts_filter_graph
from app.main import app
from app.models.edl import EditCut
from app.services.job_service import JOBS_ROOT

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_AUDIO_VIDEO = _REPO_ROOT / "test_videos" / "sample_with_audio.mp4"

client = TestClient(app)


def test_aspect_ratio_filter_generation():
    """Verify 9:16 and 1:1 filter graphs generate blurred pillarbox background."""
    cuts = [
        EditCut(clip_id=1, start_time=0.5, end_time=2.0, duration=1.5, reason="Test clip")
    ]

    # 1. 9:16 Vertical
    fg_916, maps_916 = build_cuts_filter_graph(cuts, has_audio=True, aspect_ratio="9:16")
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in fg_916
    assert "boxblur=20:5" in fg_916
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in fg_916
    assert "overlay=(W-w)/2:(H-h)/2[outv]" in fg_916

    # 2. 1:1 Square
    fg_11, maps_11 = build_cuts_filter_graph(cuts, has_audio=True, aspect_ratio="1:1")
    assert "scale=1080:1080:force_original_aspect_ratio=increase" in fg_11
    assert "boxblur=20:5" in fg_11


def test_music_tracks_and_ducking_filter():
    """Verify background music lookup and sidechain ducking filter construction."""
    tracks = get_available_music_tracks()
    assert len(tracks) >= 2
    assert "chill" in tracks or "chill_ambient" in tracks

    resolved = resolve_music_track("chill")
    assert resolved is not None
    assert resolved.exists()

    # Ducking with speech
    duck_filter, final_label = build_ducking_filter(
        speech_label="outa",
        music_input_index=1,
        music_volume=0.15,
        has_speech=True,
    )
    assert "sidechaincompress=" in duck_filter
    assert "amix=inputs=2" in duck_filter
    assert final_label == "[outa]"

    # Ducking without speech
    duck_no_speech, final_no_speech = build_ducking_filter(
        speech_label="outa",
        music_input_index=1,
        music_volume=0.20,
        has_speech=False,
    )
    assert "aloop=" in duck_no_speech
    assert "volume=0.2" in duck_no_speech


def test_week4_render_social_with_music_e2e():
    """End-to-end test rendering 9:16 video with background music ducking."""
    assert SAMPLE_AUDIO_VIDEO.exists()

    # 1. Upload video
    with open(SAMPLE_AUDIO_VIDEO, "rb") as f:
        resp = client.post(
            "/upload",
            files={"file": ("sample_week4.mp4", f, "video/mp4")},
            data={"instruction": "Create a 2 second cut."},
        )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    job_dir = JOBS_ROOT / job_id

    try:
        # 2. Plan edit
        client.post(f"/jobs/{job_id}/silence/detect")
        plan_resp = client.post(f"/jobs/{job_id}/plan")
        assert plan_resp.status_code == 200

        # 3. Test Music Tracks API
        music_resp = client.get("/jobs/music/tracks")
        assert music_resp.status_code == 200
        assert len(music_resp.json()) >= 2

        # 4. Render with 9:16 aspect ratio + chill background music
        render_resp = client.post(
            f"/jobs/{job_id}/render",
            params={
                "aspect_ratio": "9:16",
                "bg_music": "chill",
                "music_volume": 0.15,
                "burn_subtitles": False,
            },
        )
        assert render_resp.status_code == 200
        data = render_resp.json()

        assert data["status"] == "completed"
        assert data["aspect_ratio"] == "9:16"
        assert data["bg_music"] is not None
        assert data["validation"]["is_valid"] is True

        # 5. Verify output file
        out_vid = job_dir / "output" / "final_edit.mp4"
        assert out_vid.exists()
        assert out_vid.stat().st_size > 1024

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
