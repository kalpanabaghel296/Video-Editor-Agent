import json
import shutil
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.agents import director, inspector, planner, validator
from app.agents.loop import run_agent
from app.analysis.pipeline import load_analysis, run_observe
from app.editing.captions import build_captions
from app.editing.renderer import render
from app.models.edl import EDL, Clip
from app.services.jobs import JobStore
from conftest import TRANSCRIPT, make_video


def observed(job_dir):
    run_observe(job_dir, job_dir / "source" / "input.mp4")
    return load_analysis(job_dir)


# ---------- Week 1/2: observe ----------
def test_metadata_and_analysis(job_dir):
    an = observed(job_dir)
    assert an.metadata.width == 640 and an.metadata.has_audio and 14.5 < an.metadata.duration < 15.5
    assert len(an.silences) >= 2                       # the 2.5s and 1.5s gaps
    assert len(an.scenes) >= 2                         # 3 visually different blocks
    assert any(f.word == "um" and not f.approx for f in an.fillers)
    assert len(an.transcript.segments) == 3


def test_no_transcriber_is_graceful(tmp_path, sample_video):
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "input.mp4").write_bytes(sample_video.read_bytes())
    st = run_observe(tmp_path, tmp_path / "source" / "input.mp4")
    assert st["transcript"] == "failed" and st["silence"] == "done"
    assert load_analysis(tmp_path).transcript.note


def test_video_without_audio(tmp_path):
    v = make_video(tmp_path / "mute.mp4", with_audio=False)
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "input.mp4").write_bytes(v.read_bytes())
    an = observed(tmp_path)
    assert not an.metadata.has_audio and an.silences == []


# ---------- Week 3: director / planner ----------
@pytest.mark.parametrize("text,dur,aspect,cap", [
    ("Make a 30 second reel", 30, "9:16", True),
    ("YouTube video, 1.5 minutes, no captions", 90, "16:9", False),
    ("square 1:1 clip with subtitles", None, "1:1", True),
])
def test_director_rules(text, dur, aspect, cap):
    r = director.parse_rules(text)
    assert r.target_duration == dur and r.aspect_ratio == aspect and r.caption_required == cap


def test_planner_respects_target_and_cuts_silence(job_dir):
    an = observed(job_dir)
    req = director.parse_rules("make a 8 second reel about pricing")
    edl = planner.plan(req, an, str(job_dir / "source" / "input.mp4"))
    assert edl.project.duration <= 8.05
    assert any("pricing" in (c.label or "") for c in edl.clips)        # keyword boosted
    assert validator.validate(edl, an.metadata, req) == [] or \
        all(i.severity == "warning" for i in validator.validate(edl, an.metadata, req))
    assert edl.captions and edl.captions[-1].end <= edl.project.duration + 0.5


def test_filler_is_cut(job_dir):
    an = observed(job_dir)
    edl = planner.plan(director.parse_rules("clean it up"), an, "x.mp4")
    assert not any(c.source_start < 7.0 < c.source_end for c in edl.clips)  # "um" at 6.6–7.1 removed


# ---------- Week 4: validator / inspector / revise ----------
def test_validator_patches_bad_edl(job_dir):
    an = observed(job_dir)
    req = director.parse_rules("10 second video")
    edl = EDL(source_media={"path": "x", "duration": 15, "width": 640, "height": 360},
              project={"width": 640, "height": 360},
              clips=[Clip(id="a", source_start=-1, source_end=4), Clip(id="b", source_start=9, source_end=40),
                     Clip(id="c", source_start=5, source_end=5)],
              effects=[{"clip_id": "ghost"}])
    codes = {i.code for i in validator.validate(edl, an.metadata, req)}
    assert {"NEG_START", "END_BEYOND", "BAD_RANGE", "EFFECT_CLIP"} <= codes
    fixed, applied = validator.patch(edl, an.metadata, req, an.transcript)
    assert applied and fixed.project.duration <= 10.01 and not fixed.effects
    assert all(i.severity != "error" for i in validator.validate(fixed, an.metadata, req))


def test_validate_raw_schema_error():
    edl, issues = validator.validate_raw({"version": "1.0"})
    assert edl is None and issues and issues[0].code == "SCHEMA"


def test_render_full_features_and_inspect(job_dir):
    an = observed(job_dir)
    req = director.parse_rules("20 second reel with captions, music, zoom and fade transitions")
    edl = planner.plan(req, an, str(job_dir / "source" / "input.mp4"))
    res = render(edl, job_dir)
    assert res.path.exists()
    assert inspector.inspect(res.path, edl, an.metadata, req) == []


def test_inspector_detects_bad_output(job_dir, tmp_path):
    an = observed(job_dir)
    edl = planner.plan(director.parse_rules("vertical"), an, "x")
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert inspector.inspect(bad, edl, an.metadata)[0].code == "NOT_PLAYABLE"
    assert inspector.inspect(tmp_path / "missing.mp4", edl, an.metadata)[0].code == "NO_OUTPUT"


# ---------- full agent loop ----------
def make_store(tmp_path, sample_video, transcript=True):
    store = JobStore(tmp_path / "jobs")
    jid = store.create("sample.mp4")
    (store.path(jid) / "source" / "input.mp4").write_bytes(sample_video.read_bytes())
    if transcript:
        from conftest import TRANSCRIPT
        (store.path(jid) / "analysis" / "transcript.json").write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
    return store, jid


def test_agent_end_to_end(tmp_path, sample_video):
    store, jid = make_store(tmp_path, sample_video)
    run_agent(store, jid, "Make a 9:16 reel under 9 seconds with captions and music")
    s = store.load(jid)
    assert s["status"] == "done", s["error"]
    assert (store.path(jid) / "output" / "final.mp4").exists()
    assert s["explanation"] and all(v["status"] in ("done", "skipped") for v in s["stages"].values())


def test_agent_works_without_transcript(tmp_path, sample_video):
    store, jid = make_store(tmp_path, sample_video, transcript=False)
    run_agent(store, jid, "make it 6 seconds, square")
    assert store.load(jid)["status"] == "done"


def test_agent_fails_gracefully_on_broken_source(tmp_path):
    store = JobStore(tmp_path / "jobs")
    jid = store.create("junk.mp4")
    (store.path(jid) / "source" / "input.mp4").write_bytes(b"garbage")
    run_agent(store, jid, "any edit")
    s = store.load(jid)
    assert s["status"] == "failed" and s["error"] and not s["busy"]


# ---------- API ----------
def test_api_flow(tmp_path, sample_video, monkeypatch):
    from app import main
    main.store = JobStore(tmp_path / "jobs")
    c = TestClient(main.app)
    r = c.post("/api/jobs", files={"file": ("a.mp4", sample_video.read_bytes(), "video/mp4")},
               data={"instruction": "make a 7 second vertical reel with captions"})
    assert r.status_code == 200
    jid = r.json()["id"]
    for _ in range(120):
        s = c.get(f"/api/jobs/{jid}").json()
        if s["status"] in ("done", "failed"):
            break
        time.sleep(1)
    assert s["status"] == "done", s
    assert c.get(f"/api/jobs/{jid}/analysis").json()["metadata"]["width"] == 640
    assert c.get(f"/api/jobs/{jid}/edl/preview").json()["clips"]
    assert c.get(f"/api/jobs/{jid}/download").status_code == 200
    assert c.post("/api/jobs", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400
    assert c.get("/api/jobs/nope").status_code == 404


# ---------- noise removal / title card / music ----------
import re
import subprocess
import cv2
import numpy as np
from conftest import make_noisy_video


def mean_volume(path: Path) -> float:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(re.search(r"mean_volume:\s*(-?[\d.]+)", p.stderr).group(1))


def frame_at(path: Path, t: float) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, f = cap.read()
    cap.release()
    assert ok
    return f.astype(int)


def test_director_denoise_and_title():
    r = director.parse_rules('30 second reel, remove background noise, title: "Pricing Secrets"')
    assert r.denoise and r.title == "Pricing Secrets"
    assert not director.parse_rules("30 second reel").denoise


def test_noise_reduction_lowers_noise_floor(tmp_path):
    v = make_noisy_video(tmp_path / "noisy.mp4")
    levels = {}
    for flag in (False, True):
        jd = tmp_path / f"job_{flag}"
        (jd / "source").mkdir(parents=True)
        (jd / "source" / "input.mp4").write_bytes(v.read_bytes())
        run_observe(jd, jd / "source" / "input.mp4")
        an = load_analysis(jd)
        req = director.parse_rules("clean video")
        req.denoise = flag
        edl = planner.plan(req, an, str(jd / "source" / "input.mp4"))
        res = render(edl, jd)
        levels[flag] = mean_volume(res.path)
    assert levels[True] < levels[False] - 5, levels          # at least 5 dB quieter noise floor


def test_title_card_is_drawn_then_disappears(job_dir):
    an = observed(job_dir)
    src = str(job_dir / "source" / "input.mp4")
    frames = {}
    for with_title in (False, True):
        req = director.parse_rules('make it vertical' + (', title: "BIG TITLE"' if with_title else ""))
        edl = planner.plan(req, an, src)
        jd = job_dir / f"t{with_title}"
        (jd).mkdir()
        res = render(edl, jd)
        assert not res.warnings, res.warnings
        frames[with_title] = (frame_at(res.path, 1.0), frame_at(res.path, edl.project.duration - 0.5))
    early = np.abs(frames[True][0] - frames[False][0]).mean()
    late = np.abs(frames[True][1] - frames[False][1]).mean()
    assert early > 2.0 and late < 0.5, (early, late)           # visible at 1s, gone near the end


def test_music_on_video_without_audio(tmp_path):
    v = make_video(tmp_path / "mute.mp4", with_audio=False)
    jd = tmp_path / "job"
    (jd / "source").mkdir(parents=True)
    (jd / "source" / "input.mp4").write_bytes(v.read_bytes())
    an = observed(jd)
    req = director.parse_rules("10 second video with music")
    edl = planner.plan(req, an, str(jd / "source" / "input.mp4"))
    res = render(edl, jd)
    assert inspector.inspect(res.path, edl, an.metadata, req) == []
    assert mean_volume(res.path) > -60                          # music track actually present


def test_overlay_clamped_by_validator(job_dir):
    an = observed(job_dir)
    edl = planner.plan(director.parse_rules('5 second clip, title: "Hi"'), an, "x")
    edl.overlays[0].end = 99
    fixed, _ = validator.patch(edl, an.metadata, None, an.transcript)
    assert fixed.overlays[0].end <= fixed.project.duration


def test_unicode_transcript_handling(tmp_path, sample_video):
    """Ensure non-ASCII/multilingual characters (Hindi, Chinese, etc.) do not crash on Windows."""
    jd = tmp_path / "unicode_job"
    (jd / "source").mkdir(parents=True)
    (jd / "source" / "input.mp4").write_bytes(sample_video.read_bytes())
    (jd / "analysis").mkdir(parents=True)
    unicode_transcript = {
        "duration": 15.0,
        "language": "hi",
        "segments": [
            {"start": 0.5, "end": 4.5, "text": "My name is iddi and okay 您 hai raadali",
             "words": [{"text": "My", "start": 0.5, "end": 0.8},
                       {"text": "name", "start": 0.9, "end": 1.2},
                       {"text": "您", "start": 2.0, "end": 2.5}]},
            {"start": 5.0, "end": 10.0, "text": "Hello, my name is Riddhi, नमस्ते मैं B.Tech student हूँ",
             "words": [{"text": "Hello", "start": 5.0, "end": 5.5},
                       {"text": "Riddhi", "start": 6.0, "end": 6.6},
                       {"text": "student", "start": 8.0, "end": 8.6}]}
        ]
    }
    (jd / "analysis" / "transcript.json").write_text(json.dumps(unicode_transcript, ensure_ascii=False), encoding="utf-8")
    status = run_observe(jd, jd / "source" / "input.mp4")
    assert status["fillers"] == "done"
    an = load_analysis(jd)
    assert len(an.transcript.segments) == 2
    assert "नमस्ते" in an.transcript.segments[1].text


def test_user_prompt_advanced_features_and_render(job_dir):
    an = observed(job_dir)
    prompt = (
        "Edit this video into a crisp, high-quality 10-second vertical clip (9:16 aspect ratio, 1080x1920) "
        "centered on the speaker in a medium close-up frame. Trim the timeline strictly to the best 10 seconds "
        "of her clear self-introduction ('Hello, my name is Riddhi, I am a B.Tech final year student...'), "
        "cutting out all previous bloopers, giggles, awkward pauses, and filler words like 'umm' and 'aah'. "
        "Remove all background chatter, side conversations, and ambient library noise, isolating and enhancing "
        "the main voice for crisp, studio-quality dialogue. Apply natural cinematic color correction to balance "
        "uneven lighting, reduce harsh overhead glare and glasses reflections, and give warm, healthy skin tones. "
        "Add auto-synced, punchy animated captions in the lower-third center with a bold sans-serif font "
        "and highlighted active words."
    )
    req = director.parse_rules(prompt)
    assert req.target_duration == 10.0
    assert req.aspect_ratio == "9:16"
    assert req.target_width == 1080
    assert req.target_height == 1920
    assert req.denoise is True
    assert req.color_grade is True
    assert req.zoom is True
    assert req.caption_required is True
    assert "riddhi" in req.keywords or "hello" in req.keywords

    src = str(job_dir / "source" / "input.mp4")
    edl = planner.plan(req, an, src)
    assert edl.project.width == 1080
    assert edl.project.height == 1920
    assert edl.project.color_grade is True
    assert edl.caption_settings.enabled is True
    assert any("cinematic color" in ex.lower() for ex in edl.explanation)

    res = render(edl, job_dir)
    assert res.path.exists()
    issues = inspector.inspect(res.path, edl, an.metadata, req)
    errs = [i for i in issues if i.severity == "error"]
    assert not errs, errs


def test_source_captions_generation_and_api(tmp_path, sample_video):
    from app.editing.captions import transcript_to_captions, write_vtt, write_srt
    from app.models.transcript import Transcript, Segment, Word
    from app import main

    store, jid = make_store(tmp_path, sample_video, transcript=True)
    jd = store.path(jid)

    # 1. Observe generates source.vtt and source.srt
    st = run_observe(jd, jd / "source" / "input.mp4")
    assert (jd / "analysis" / "source.vtt").exists()
    assert (jd / "analysis" / "source.srt").exists()
    assert (jd / "analysis" / "source_captions.json").exists()

    vtt_content = (jd / "analysis" / "source.vtt").read_text(encoding="utf-8")
    assert vtt_content.startswith("WEBVTT")
    assert "-->" in vtt_content

    srt_content = (jd / "analysis" / "source.srt").read_text(encoding="utf-8")
    assert "-->" in srt_content

    # 2. Test API endpoints
    main.store = store
    c = TestClient(main.app)

    # VTT track for video player
    r_vtt = c.get(f"/api/jobs/{jid}/captions?kind=source&format=vtt")
    assert r_vtt.status_code == 200
    assert "text/vtt" in r_vtt.headers.get("content-type", "")
    assert "WEBVTT" in r_vtt.text

    # SRT track
    r_srt = c.get(f"/api/jobs/{jid}/captions?kind=source&format=srt")
    assert r_srt.status_code == 200
    assert "-->" in r_srt.text

    # JSON captions in analysis
    r_an = c.get(f"/api/jobs/{jid}/analysis")
    assert r_an.status_code == 200
    caps_data = r_an.json().get("captions", [])
    assert len(caps_data) > 0
    assert "start" in caps_data[0] and "text" in caps_data[0]

    # Subtitle download
    r_dl = c.get(f"/api/jobs/{jid}/captions/download?kind=source&format=srt")
    assert r_dl.status_code == 200
    assert "attachment" in r_dl.headers.get("content-disposition", "")


def test_transcript_to_captions_word_accuracy():
    from app.editing.captions import transcript_to_captions, write_vtt, write_srt
    from app.models.transcript import Transcript, Segment, Word

    tr = Transcript(
        duration=10.0,
        language="en",
        segments=[
            Segment(
                start=0.0,
                end=4.0,
                text="Hello guys this video I am making AI Agentic",
                words=[
                    Word(text="Hello", start=0.0, end=0.5),
                    Word(text="guys", start=0.5, end=1.0),
                    Word(text="this", start=1.0, end=1.5),
                    Word(text="video", start=1.5, end=2.0),
                    Word(text="I", start=2.0, end=2.3),
                    Word(text="am", start=2.3, end=2.6),
                    Word(text="making", start=2.6, end=3.0),
                    Word(text="AI", start=3.0, end=3.5),
                    Word(text="Agentic", start=3.5, end=4.0),
                ]
            )
        ]
    )
    caps = transcript_to_captions(tr)
    assert len(caps) >= 1
    # Check that word details are preserved
    assert any(c.words for c in caps)
    assert caps[0].start == 0.0
    assert caps[-1].end <= 4.05
    assert "AI Agentic" in " ".join(c.text for c in caps)



# ---- regression tests for the real-world 15s Hinglish prompt ------------------------------------
REAL_PROMPT = ('Trim the video to exactly the first 15 seconds (from 00:00:00 to 00:00:15). Captions at the bottom. '
               'Caption Breakdown: 00:00 – 00:03: "Hello guys, yeh video mein AI Agent ke baare mein bata rahi hoon" '
               '00:03 – 00:07: "Jisme aap koi bhi video dalo, woh edit ho jayegi!" '
               'Add subtle whoosh transitions or punch-in zoom cuts and pop / ding SFX.')


def test_director_parses_window_script_and_sfx():
    from app.agents.director import parse_rules
    r = parse_rules(REAL_PROMPT)
    assert (r.target_start, r.target_end, r.target_duration) == (0.0, 15.0, 15.0)
    assert len(r.caption_script) == 2 and r.caption_script[1].start == 3.0
    assert r.sfx and r.zoom
    assert not r.remove_silence and not r.remove_fillers      # exact window = nothing dropped inside it


def test_time_formats():
    from app.agents.director import parse_rules
    assert parse_rules("first 20 seconds").target_end == 20.0
    assert parse_rules("cut 1:05 to 1:35").target_duration == 30.0
    assert parse_rules("make it 45 sec reel").target_duration == 45.0


def test_real_prompt_end_to_end(job_dir):
    """Exact 15s window, prompt-dictated captions, zoom cuts, SFX mixed, audio kept."""
    import json
    from app.agents import loop
    from app.services.jobs import JobStore
    from app.utils.ffmpeg import ffprobe_json
    store = JobStore(job_dir.parent / "jobs_real")
    jid = store.create("x.mp4")
    shutil.copy(job_dir / "source" / "input.mp4", store.path(jid) / "source" / "input.mp4")
    loop.run_agent(store, jid, REAL_PROMPT)
    job = store.load(jid)
    assert job["status"] == "done", job.get("error")
    edl = json.loads((store.path(jid) / "edl.json").read_text(encoding="utf-8"))
    assert edl["captions"] and edl["captions"][0]["text"].startswith("Hello guys")
    assert len(edl["clips"]) >= 2 and edl["audio"]["sfx_events"]
    info = ffprobe_json(store.path(jid) / "output" / "final.mp4")
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - 15.0) < 0.5


def test_range_requests_and_transcript_edit(job_dir):
    from fastapi.testclient import TestClient
    from app import main
    main.store.root = job_dir.parent / "jobs_api"
    main.store.root.mkdir(exist_ok=True)
    c = TestClient(main.app)
    jid = main.store.create("x.mp4")
    shutil.copy(job_dir / "source" / "input.mp4", main.store.path(jid) / "source" / "input.mp4")
    r = c.get(f"/api/jobs/{jid}/video", params={"kind": "source"}, headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.headers["content-range"].startswith("bytes 0-9/")
    (main.store.path(jid) / "analysis" / "transcript.json").write_text(
        json.dumps(TRANSCRIPT), encoding="utf-8")
    segs = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in TRANSCRIPT["segments"]]
    segs[0]["text"] = "Corrected caption text"
    assert c.put(f"/api/jobs/{jid}/transcript", json={"segments": segs}).status_code == 200
    saved = json.loads((main.store.path(jid) / "analysis" / "transcript.json").read_text(encoding="utf-8"))
    assert saved["segments"][0]["text"] == "Corrected caption text"
