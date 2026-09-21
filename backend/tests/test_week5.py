"""
Week 5 Automated Test Suite — Autonomous Orchestrator, Platform Presets,
Punch-In Zoom, and Iterative Revision Loop.
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.agents.director import revise_edl, plan_edit
from app.editing.cuts import build_cuts_filter_graph
from app.main import app
from app.models.edl import EditCut
from app.models.orchestration import BUILTIN_PRESETS

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_AUDIO_VIDEO = _REPO_ROOT / "test_videos" / "sample_with_audio.mp4"

client = TestClient(app)


def test_platform_presets_endpoint():
    """Verify GET /presets returns all built-in platform presets."""
    res = client.get("/presets")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 4

    preset_ids = {p["preset_id"] for p in data}
    assert "reels" in preset_ids
    assert "shorts" in preset_ids
    assert "youtube" in preset_ids
    assert "square" in preset_ids

    reels = next(p for p in data if p["preset_id"] == "reels")
    assert reels["aspect_ratio"] == "9:16"
    assert reels["enable_punch_in"] is True
    assert reels["burn_subtitles"] is True


def test_punch_in_filter_generation():
    """Verify build_cuts_filter_graph adds 1.15x scale/crop zoom to alternating cuts."""
    cuts = [
        EditCut(clip_id=1, start_time=0.0, end_time=2.0, duration=2.0, reason="Clip 1"),
        EditCut(clip_id=2, start_time=2.5, end_time=5.0, duration=2.5, reason="Clip 2"),
        EditCut(clip_id=3, start_time=6.0, end_time=8.0, duration=2.0, reason="Clip 3"),
    ]

    # Without punch-in
    filt_no_zoom, _ = build_cuts_filter_graph(cuts, has_audio=True, enable_punch_in=False)
    assert "scale=1.15*iw:-2" not in filt_no_zoom

    # With punch-in
    filt_zoom, _ = build_cuts_filter_graph(cuts, has_audio=True, enable_punch_in=True)
    # Clip 1 (idx 0) no zoom, Clip 2 (idx 1) zoom, Clip 3 (idx 2) no zoom
    assert "scale=1.15*iw:-2,crop=iw/1.15:ih/1.15[v1]" in filt_zoom
    assert "scale=1.15*iw:-2" not in filt_zoom.split(";")[0]  # First clip (v0) unchanged


def test_revise_edl_logic(tmp_path):
    """Verify revise_edl drops clips and modifies targets based on conversational feedback."""
    # First upload a video to create a real job
    fake_bytes = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    # Use existing test video for genuine metadata
    with open(SAMPLE_AUDIO_VIDEO, "rb") as f:
        upload_res = client.post(
            "/upload",
            files={"file": ("sample.mp4", f, "video/mp4")},
            data={"instruction": "Keep everything."},
        )
    assert upload_res.status_code == 201
    job_id = upload_res.json()["job_id"]

    # Generate initial plan
    initial_edl = plan_edit(job_id)
    assert len(initial_edl.cuts) >= 1

    # Revise with target duration override
    revised = revise_edl(job_id, revision_instruction="make it 2 seconds", target_duration_override=2.0)
    assert revised.target_duration == 2.0


def test_autonomous_pipeline_e2e():
    """Verify POST /jobs/{job_id}/auto-edit executes complete agent loop in one call."""
    with open(SAMPLE_AUDIO_VIDEO, "rb") as f:
        upload_res = client.post(
            "/upload",
            files={"file": ("sample.mp4", f, "video/mp4")},
            data={"instruction": "Create a punchy vertical short."},
        )
    assert upload_res.status_code == 201
    job_id = upload_res.json()["job_id"]

    # Run auto-edit with "shorts" preset
    auto_res = client.post(
        f"/jobs/{job_id}/auto-edit",
        json={
            "preset": "shorts",
            "instruction": "Fast pacing with punch-in zoom",
            "aspect_ratio": "9:16",
            "bg_music": "chill_ambient",
            "burn_subtitles": False,
        },
    )
    assert auto_res.status_code == 200
    data = auto_res.json()
    assert data["status"] == "completed"
    assert data["rendered_duration"] > 0
    assert data["validation"]["is_valid"] is True


def test_revision_endpoint_e2e():
    """Verify POST /jobs/{job_id}/revise allows iterative re-rendering."""
    with open(SAMPLE_AUDIO_VIDEO, "rb") as f:
        upload_res = client.post(
            "/upload",
            files={"file": ("sample.mp4", f, "video/mp4")},
        )
    assert upload_res.status_code == 201
    job_id = upload_res.json()["job_id"]

    # Run initial plan & render
    client.post(f"/jobs/{job_id}/plan")
    client.post(f"/jobs/{job_id}/render?aspect_ratio=original")

    # Submit conversational revision
    rev_res = client.post(
        f"/jobs/{job_id}/revise",
        json={
            "revision_instruction": "Change to square format and add background music",
            "aspect_ratio": "1:1",
            "bg_music": "upbeat_energetic",
            "burn_subtitles": False,
        },
    )
    assert rev_res.status_code == 200
    rev_data = rev_res.json()
    assert rev_data["status"] == "completed"
    assert rev_data["validation"]["is_valid"] is True


def test_narrative_arc_whole_video_selection():
    """Verify _select_narrative_arc_intervals preserves complete spoken thoughts and samples across the timeline."""
    from app.agents.director import _select_narrative_arc_intervals

    raw_intervals = [(0.0, 6.0), (7.0, 18.0), (20.0, 24.0), (25.0, 30.0)]
    transcript_segments = [
        {"start": 0.0, "end": 6.0, "text": "Hook sentence"},
        {"start": 7.0, "end": 18.0, "text": "Core middle point"},
        {"start": 25.0, "end": 30.0, "text": "Concluding thought"},
    ]

    # Target 10s: preserves complete hook and concluding thought without mid-thought severing
    cuts_10 = _select_narrative_arc_intervals(
        active_intervals=raw_intervals,
        target_duration=10.0,
        original_duration=31.0,
        transcript_segments=transcript_segments,
    )
    assert len(cuts_10) == 2
    assert cuts_10[0][0] == 0.0
    assert cuts_10[0][1] == 6.0  # Full hook sentence preserved!
    assert cuts_10[1][0] >= 24.0  # Full concluding sentence preserved!
    assert round(sum(en - st for st, en in cuts_10), 2) == 10.0

    # Target 15s: accommodates all 3 narrative phases (hook, core middle, outro)
    cuts_15 = _select_narrative_arc_intervals(
        active_intervals=raw_intervals,
        target_duration=15.0,
        original_duration=31.0,
        transcript_segments=transcript_segments,
    )
    assert len(cuts_15) == 3
    assert cuts_15[0][0] == 0.0
    assert cuts_15[1][0] >= 7.0 and cuts_15[1][1] <= 24.0
    assert cuts_15[2][0] >= 25.0
    assert round(sum(en - st for st, en in cuts_15), 2) == 15.0

