"""Week 7: FFmpeg detection, Hinglish prompts, platform presets, chat revise, best-take selection,
face-aware reframing, caption presets, reference-style matching and the new API endpoints."""
import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from conftest import make_video
from app import config
from app.agents import director, presets, revise
from app.analysis import reference, takes
from app.editing import captions as cap
from app.editing import reframe
from app.models.edl import Caption, CaptionSettings, CaptionWord
from app.utils import ffmpeg as ff
from app.utils.errors import friendly


def ffrun(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)], check=True)


# ----------------------------------------------------------------------------- FFmpeg / errors
def test_missing_ffmpeg_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(ff, "find_binary", lambda name: None)
    try:
        ff.run(["ffprobe", "-version"])
        raise AssertionError("expected FFmpegMissing")
    except ff.FFmpegMissing as e:
        assert "nahi mila" in str(e) and ("FFMPEG_PATH" in str(e) or "winget" in str(e))
    assert ff.ffmpeg_status()["ok"] is False


def test_ffmpeg_path_env_is_honoured(tmp_path, monkeypatch):
    real = shutil.which("ffmpeg")
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    for n in ("ffmpeg", "ffprobe"):
        shutil.copy(shutil.which(n), fake_dir / (n + (".exe" if ff.sys.platform == "win32" else "")))
    ff.reset_cache()
    monkeypatch.setattr(config, "FFMPEG_PATH", str(fake_dir))
    assert ff.find_binary("ffmpeg").startswith(str(fake_dir))
    ff.reset_cache()
    assert real


def test_friendly_errors():
    assert "ffmpeg" in friendly(FileNotFoundError("[WinError 2] The system cannot find the file specified")).lower()
    assert "Application Control" in friendly(RuntimeError("An Application Control policy has blocked this file"))
    assert "Whisper" in friendly(RuntimeError("Max retries exceeded with url huggingface.co"))


# ----------------------------------------------------------------------------- prompts
def test_plain_word_short_is_not_youtube_shorts():
    r = director.parse_rules("make it short remove the fumbling and also add the caption")
    assert r.platform is None and r.aspect_ratio == "original" and r.asks_edit and r.caption_required
    assert director.parse_rules("make a youtube short about pricing").platform == "shorts"


@pytest.mark.parametrize("text,check", [
    ("isko chhota kar do, galtiyan hata do aur caption laga do", lambda r: r.asks_edit and r.smart_cuts and r.caption_required),
    ("caption chhote karo", lambda r: r.caption_size == "small"),
    ("caption bade karo", lambda r: r.caption_size == "large"),
    ("zoom kam karo", lambda r: r.zoom and r.zoom_strength == "subtle"),
    ("zyada zoom lagao", lambda r: r.zoom and r.zoom_strength == "strong"),
    ("no zoom please", lambda r: not r.zoom),
    ("music hata do", lambda r: not r.music_required),
    ("add calm music but no sound effects", lambda r: r.music_required and not r.sfx),
    ("sound effects nahi chahiye, caption mat lagao", lambda r: not r.sfx and not r.caption_required),
    ("30 second reel with hormozi captions", lambda r: r.caption_style == "hormozi" and r.target_duration == 30),
    ("boxed captions please", lambda r: r.caption_style == "boxed"),
    ("karaoke captions", lambda r: r.caption_style == "karaoke"),
    ("vertical video, follow my face", lambda r: r.reframe == "face" and r.aspect_ratio == "9:16"),
    ("vertical video with blurred background", lambda r: r.reframe == "blur" and r.aspect_ratio == "9:16"),
    ("vertical video center crop", lambda r: r.reframe == "center"),
])
def test_hinglish_and_new_style_words(text, check):
    assert check(director.parse_rules(text)), text


def test_platform_presets():
    long_landscape = SimpleNamespace_meta(300, 1920, 1080)
    r = director.understand("make an instagram reel", long_landscape)
    assert (r.target_width, r.target_height) == (1080, 1920)
    assert r.caption_style == "hormozi" and r.caption_margin == 0.22
    assert r.target_duration == 45 and any("best" in n for n in r.notes)        # 5-min video -> best ~45s
    r2 = director.understand("make a 20 second tiktok", long_landscape)
    assert r2.target_duration == 20                                           # explicit length wins
    r3 = director.understand("make a linkedin post", SimpleNamespace_meta(30, 1920, 1080))
    assert r3.aspect_ratio == "1:1" and r3.caption_style == "boxed" and r3.target_duration is None
    assert {p["key"] for p in presets.list_presets()} >= {"reels", "shorts", "tiktok", "linkedin", "youtube"}


def SimpleNamespace_meta(duration, w, h):
    from app.models.metadata import VideoMetadata
    return VideoMetadata(duration=duration, width=w, height=h, fps=30, has_audio=True)


# ----------------------------------------------------------------------------- chat revise
def test_chat_revise_conversation():
    prev = "make it short remove the fumbling and also add the caption with calm music and zoom"
    cur = prev
    for msg in ["zoom kam karo", "caption chhote karo", "make it 20 seconds", "music hata do", "hormozi captions laga do",
                "black and white kar do", "make it a reel", "follow my face", "sound effects add karo"]:
        rev = revise.interpret(msg, cur, current_duration=25)
        assert rev.understood and rev.changes and rev.reply.startswith("Theek hai")
        cur = rev.instruction
    r = director.parse_rules(cur)
    assert r.zoom and r.zoom_strength == "subtle"
    assert r.caption_style == "hormozi" and r.caption_size == "small"            # earlier "small" survives the style change
    assert r.target_duration == 20 and not r.music_required and r.sfx
    assert r.look.preset == "bw" and r.platform == "reels" and r.reframe == "face"
    assert r.asks_edit and r.smart_cuts                                           # the original request is still there


def test_chat_relative_length_and_unknown_message():
    rev = revise.interpret("thoda aur chhota karo", "make a 30 second video", current_duration=30)
    assert director.parse_rules(rev.instruction).target_duration == 24
    rev = revise.interpret("asdf qwerty", "make a 30 second video")
    assert not rev.understood and "30 second" in rev.instruction
    # a later number replaces the old one instead of competing with it
    rev = revise.interpret("make it 12 seconds", "make a 30 second reel")
    assert director.parse_rules(rev.instruction).target_duration == 12


# ----------------------------------------------------------------------------- best take
def _seg(start, text, wps=2.6, gaps=None):
    toks, t, words = text.split(), start, []
    for i, tok in enumerate(toks):
        words.append(NS(text=tok, start=round(t, 3), end=round(t + 1 / wps, 3)))
        t += 1 / wps + (gaps or {}).get(i, 0)
    return NS(start=start, end=round(t, 3), text=text, words=words)


def test_best_take_is_not_always_the_last_one():
    clean_first = NS(segments=[_seg(0, "This is the best tip for editing videos fast"),
                               _seg(8, "um this is the best tip uh for editing videos fast", gaps={3: 1.0})])
    _, g = takes.analyze(clean_first)
    assert g[0].best == 0 and g[0].cuts[0][2] == 1                                # the messy LATER take is dropped
    clean_last = NS(segments=[_seg(0, "This is is the best tip tip for editing videos fast"),
                              _seg(8, "This is the best tip for editing videos fast")])
    _, g = takes.analyze(clean_last)
    assert g[0].best == 1 and g[0].cuts[0][2] == 0


def test_three_takes_with_an_abandoned_one_and_non_adjacent_takes():
    tr = NS(segments=[_seg(1, "um so today I I want to talk about AI agents", gaps={4: 0.9}),
                      _seg(9, "So today I want to talk about AI agents and video editing"),
                      _seg(16, "So today I want to talk about"), _seg(22, "Let us begin with the basics")])
    _, g = takes.analyze(tr)
    assert g[0].members == [0, 1, 2] and g[0].best == 1 and len(g[0].cuts) == 2
    tr = NS(segments=[_seg(0, "The biggest mistake beginners make is skipping the audio"),
                      _seg(8, "Anyway moving on to something totally different now"),
                      _seg(15, "The biggest mistake beginners make is skipping the audio quality")])
    _, g = takes.analyze(tr)
    assert len(g) == 1 and g[0].members == [0, 2] and g[0].best == 2


def test_different_sentences_are_never_grouped():
    tr = NS(segments=[_seg(0, "Welcome to my channel where I talk about cooking"), _seg(6, "Today I will show you how to make pasta"),
                      _seg(12, "First boil the water and add some salt")])
    assert takes.analyze(tr)[1] == []


def test_audio_level_breaks_a_tie():
    u = takes.units_from_transcript(NS(segments=[_seg(0, "this is the final pricing for the product today"),
                                                 _seg(8, "this is the final pricing for the product today")]))
    u[0].energy_db, u[1].energy_db = -18.0, -32.0
    assert takes.choose_best_takes(u)[0].best == 0


def test_fumble_detector_uses_best_take():
    from app.analysis.fumbles import detect_fumbles
    from app.models.transcript import Segment, Transcript, Word
    segs = []
    for s in (_seg(0, "this is the first take of my intro today", gaps={3: 1.0}), _seg(10, "this is the first take of my intro today")):
        segs.append(Segment(start=s.start, end=s.end, text=s.text, words=[Word(text=w.text, start=w.start, end=w.end) for w in s.words]))
    rt = [f for f in detect_fumbles(Transcript(duration=30, segments=segs), [], [], 30) if f.kind == "retake"]
    assert len(rt) == 1 and rt[0].start < 1 and "kept" in rt[0].reason


# ----------------------------------------------------------------------------- face-aware reframing
def red_detector(frame):
    m = (frame[:, :, 2] > 200) & (frame[:, :, 1] < 60) & (frame[:, :, 0] < 60)
    ys, xs = np.nonzero(m)
    if xs.size < 50:
        return None
    h, w = frame.shape[:2]
    return (float(xs.mean() / w), float(ys.mean() / h), 0.1)


@pytest.fixture(scope="module")
def moving_subject(tmp_path_factory):
    p = tmp_path_factory.mktemp("rf") / "wide.mp4"
    ffrun("-f", "lavfi", "-i", "color=c=0x404040:s=1280x720:r=25:d=6", "-f", "lavfi", "-i", "color=c=red:s=120x120:r=25:d=6",
          "-f", "lavfi", "-i", "sine=f=440:d=6", "-filter_complex", "[0][1]overlay=x='80+1000*t/6':y=300[v]", "-map", "[v]",
          "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(p))
    return p


def test_crop_geometry_and_path_logic():
    assert reframe.crop_geometry(1920, 1080, 1080, 1920) == ("x", pytest.approx(0.3164, abs=1e-3))
    assert reframe.crop_geometry(1080, 1920, 1080, 1920) == (None, 1.0)
    assert reframe.crop_geometry(1080, 1920, 1920, 1080)[0] == "y"
    # face jitters around 0.5 -> virtual tripod holds still; a face that never shows up -> no path
    jitter = [(i * 0.25, (0.5 + (0.01 if i % 2 else -0.01), 0.4, 0.2)) for i in range(20)]
    pts, ratio = reframe.build_path(jitter, 5.0, 0.3164)
    assert ratio == 1.0 and max(c for _, c in pts) - min(c for _, c in pts) < 0.02
    assert reframe.build_path([(i * 0.25, None) for i in range(20)], 5.0, 0.3)[0] == []
    # a real move is followed, but never faster than MAX_SPEED
    move = [(i * 0.25, (0.3 + i * 0.02, 0.4, 0.2)) for i in range(20)]
    pts, _ = reframe.build_path(move, 5.0, 0.3164)
    assert pts[-1][1] > pts[0][1] + 0.15
    for (t0, c0), (t1, c1) in zip(pts, pts[1:]):
        assert abs(c1 - c0) / max(t1 - t0, 1e-3) <= reframe.MAX_SPEED + 1e-6
    assert len(pts) <= 40 and "if(lt(t," in reframe.crop_filter("x", pts, 1080, 1920)


def test_tracked_crop_keeps_the_subject_in_frame(moving_subject, tmp_path):
    import cv2
    from app.editing.renderer import build_base_cmd
    from app.models.edl import EDL, Clip, ProjectSettings, Reframe, SourceMedia
    axis, pts, ratio = reframe.track_clip(moving_subject, 0, 6, 1280, 720, 360, 640, detector=red_detector)
    assert axis == "x" and ratio == 1.0
    edl = EDL(source_media=SourceMedia(path=str(moving_subject), duration=6, width=1280, height=720, fps=25, has_audio=True),
              project=ProjectSettings(aspect_ratio="9:16", width=360, height=640, fps=25, fit="crop"),
              clips=[Clip(id="c1", source_start=0, source_end=6)],
              reframes=[Reframe(clip_id="c1", axis=axis, points=pts, face_ratio=ratio)])
    seen = {}
    for name, fx in (("tracked", True), ("center", False)):
        out = tmp_path / f"{name}.mp4"
        ff.run(build_base_cmd(edl, out, use_effects=fx))
        cp, xs = cv2.VideoCapture(str(out)), []
        assert (int(cp.get(3)), int(cp.get(4))) == (360, 640)
        for t in (0.2, 1.5, 3.0, 4.5, 5.6):
            cp.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            xs.append(red_detector(cp.read()[1]))
        seen[name] = xs
    assert all(x is not None and 0.1 < x[0] < 0.9 for x in seen["tracked"])       # always in frame
    assert any(x is None for x in seen["center"])                                  # centre crop loses it


def test_blur_fill_renders_vertical(moving_subject, tmp_path):
    import cv2
    from app.editing.renderer import build_base_cmd
    from app.models.edl import EDL, Clip, ProjectSettings, SourceMedia
    edl = EDL(source_media=SourceMedia(path=str(moving_subject), duration=6, width=1280, height=720, fps=25, has_audio=True),
              project=ProjectSettings(aspect_ratio="9:16", width=360, height=640, fps=25, fit="blur"),
              clips=[Clip(id="c1", source_start=0, source_end=6)])
    out = tmp_path / "blur.mp4"
    ff.run(build_base_cmd(edl, out, use_effects=True))
    cp = cv2.VideoCapture(str(out))
    cp.set(cv2.CAP_PROP_POS_MSEC, 3000)
    f = cp.read()[1]
    assert f.shape[:2] == (640, 360)
    assert f[:40].mean() > 20                                                       # top bar is a blurred copy, not black


# ----------------------------------------------------------------------------- caption presets
def _caps(preset):
    words = "Stop doing this one thing in your edits if you want more views today".split()
    ws, t = [], 0.2
    for w in words:
        ws.append(CaptionWord(text=w, start=round(t, 3), end=round(t + 0.35, 3)))
        t += 0.35
    mw, ms = cap.chunk_params(preset)
    return [Caption(start=c[0].start, end=c[-1].end, text=" ".join(x.text for x in c), words=c) for c in cap._chunk(ws, mw, ms)]


@pytest.mark.parametrize("preset", ["classic", "hormozi", "minimal", "boxed", "karaoke"])
def test_every_caption_preset_renders(preset, tmp_path):
    ass = tmp_path / "c.ass"
    cap.write_ass(_caps(preset), ass, 360, 640, CaptionSettings(enabled=True, preset=preset))
    out = tmp_path / "o.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x2a6f97:s=360x640:r=25:d=4",
                    "-vf", f"ass={ass.name}", "-pix_fmt", "yuv420p", str(out)], check=True, cwd=str(tmp_path))
    assert out.stat().st_size > 1000


def test_caption_preset_details():
    assert max(len(c.words) for c in _caps("hormozi")) <= 3 and max(len(c.words) for c in _caps("classic")) > 3
    txt = (lambda p: (cap.write_ass(_caps("hormozi"), p, 360, 640, CaptionSettings(preset="hormozi")), p.read_text(encoding="utf-8"))[1])
    import tempfile
    body = txt(Path(tempfile.mkdtemp()) / "h.ass")
    assert "STOP" in body and "\\c&H00E5FF&" in body                              # upper-case + highlighted active word
    small = cap.write_ass(_caps("classic"), Path(tempfile.mkdtemp()) / "s.ass", 360, 640, CaptionSettings(size="small")).read_text(encoding="utf-8")
    large = cap.write_ass(_caps("classic"), Path(tempfile.mkdtemp()) / "l.ass", 360, 640, CaptionSettings(size="large")).read_text(encoding="utf-8")
    size = lambda s: int(s.split("Style: Default,")[1].split(",")[1])
    assert size(small) < size(large)
    safe = cap.write_ass(_caps("classic"), Path(tempfile.mkdtemp()) / "m.ass", 360, 640, CaptionSettings(margin_pct=0.22)).read_text(encoding="utf-8")
    assert safe.split("Style: Default,")[1].split("\n")[0].split(",")[-2] == str(int(640 * 0.22))
    hindi = [Caption(start=0, end=1, text="नमस्ते दोस्तों", words=[CaptionWord(text="नमस्ते", start=0, end=.5), CaptionWord(text="दोस्तों", start=.5, end=1)])]
    assert "Nirmala UI" in cap.write_ass(hindi, Path(tempfile.mkdtemp()) / "d.ass", 360, 640).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- reference style
def _concat_video(path, colors, shot, size):
    ins = []
    for c in colors:
        ins += ["-f", "lavfi", "-i", "testsrc2=s=%s:r=25:d=%s" % (size, shot) if c == "test" else "color=c=%s:s=%s:r=25:d=%s" % (c, size, shot)]
    fc = "".join(f"[{i}:v]" for i in range(len(colors))) + f"concat=n={len(colors)}:v=1:a=0[v]"
    ffrun(*ins, "-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
    return path


def test_reference_profile_and_application(tmp_path):
    ref = _concat_video(tmp_path / "ref.mp4", ["orange", "test", "gold", "red", "test", "orange", "gold", "test"], 1.0, "360x640")
    prof = reference.analyze(ref)
    assert prof["aspect_ratio"] == "9:16" and prof["cuts"] == 7 and 0.9 <= prof["avg_shot_len"] <= 1.1
    dull = _concat_video(tmp_path / "dull.mp4", ["0x303840"], 8.0, "640x360")
    src_look = reference.look_stats(dull)
    sl = reference.look_sliders(prof["look"], src_look)
    assert sl["brightness"] > 0 and sl["warmth"] > 0 and all(-50 <= v <= 50 for v in sl.values())
    assert reference.look_sliders(src_look, src_look) == {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0, "warmth": 0.0}
    r = director.parse_rules("make a video")
    notes = reference.apply_to_requirements(r, prof, src_look, None, 8.0)
    assert r.aspect_ratio == "9:16" and r.zoom and r.style == "fast" and r.shot_len == 1.2 and notes
    # the prompt always wins: an explicit aspect ratio and an explicit brightness are kept
    r2 = director.parse_rules("make a 16:9 video, brighter")
    b = r2.look.brightness
    reference.apply_to_requirements(r2, prof, src_look, None, 8.0)
    assert r2.aspect_ratio == "16:9" and r2.look.brightness == b


def test_reference_length_option(tmp_path):
    prof = {"duration": 10.0, "aspect_ratio": "9:16", "avg_shot_len": 3.0, "look": {"brightness": .4, "contrast": .2, "saturation": .3, "warmth": 0}}
    r = director.parse_rules("make a video")
    reference.apply_to_requirements(r, prof, None, {"length": True, "look": False}, 60.0)
    assert r.target_duration == 10
    r = director.parse_rules("make a video")
    reference.apply_to_requirements(r, prof, None, None, 60.0)
    assert r.target_duration is None                                               # length matching is opt-in


# ----------------------------------------------------------------------------- planner + API
def _observed_job(job_dir):
    from app.analysis.pipeline import load_analysis, run_observe
    run_observe(job_dir, job_dir / "source" / "input.mp4")
    return load_analysis(job_dir)


def test_planner_uses_ui_caption_style_and_reference(job_dir):
    from app.agents import planner
    an = _observed_job(job_dir)
    src = str(job_dir / "source" / "input.mp4")
    (job_dir / "assets").mkdir(exist_ok=True)
    (job_dir / "assets" / "caption_style.json").write_text(json.dumps({"preset": "karaoke", "size": "large"}))
    prof = {"duration": 10.0, "aspect_ratio": "9:16", "avg_shot_len": 1.5, "look": {"brightness": .5, "contrast": .3, "saturation": .5, "warmth": .05}}
    (job_dir / "assets" / "reference.json").write_text(json.dumps({"profile": prof, "options": {}, "enabled": True}))
    req = director.understand("make a reel with hormozi captions", an.metadata)
    edl = planner.plan(req, an, src)
    assert edl.caption_settings.preset == "karaoke" and edl.caption_settings.size == "large"       # UI choice wins over the prompt
    assert edl.project.aspect_ratio == "9:16" and any("Style:" in e for e in edl.explanation)
    assert max(len(c.words) for c in edl.captions) <= 5
    # reference switched off -> ignored
    (job_dir / "assets" / "reference.json").write_text(json.dumps({"profile": prof, "options": {}, "enabled": False}))
    req2 = director.understand("make a video", an.metadata)
    assert planner.plan(req2, an, src).project.aspect_ratio == "original"


def test_full_run_with_face_tracking_and_presets(moving_subject, tmp_path, monkeypatch):
    """prompt -> plan -> render -> inspect on a wide video: 9:16 output, subject followed, hormozi captions burned in."""
    import cv2
    from app.agents import loop
    from app.services.jobs import JobStore
    monkeypatch.setattr(reframe, "detect_face", red_detector)
    monkeypatch.setattr(reframe, "_load_cascades", lambda: [object()])
    store = JobStore(tmp_path / "jobs")
    jid = store.create("wide.mp4")
    shutil.copy(moving_subject, store.path(jid) / "source" / "input.mp4")
    tr = {"language": "en", "duration": 6.0, "segments": [{"start": 0.3, "end": 5.5, "text": "Stop doing this one thing in your edits today", "words": [
        {"text": w, "start": round(0.3 + i * 0.5, 2), "end": round(0.75 + i * 0.5, 2)} for i, w in enumerate("Stop doing this one thing in your edits today".split())]}]}
    (store.path(jid) / "analysis" / "transcript.json").write_text(json.dumps(tr))
    loop.run_agent(store, jid, "make an instagram reel with hormozi captions, follow my face")
    job = store.load(jid)
    assert job["status"] == "done", job.get("error")
    edl = json.loads((store.path(jid) / "edl.json").read_text(encoding="utf-8"))
    assert edl["project"]["width"] == 1080 and edl["project"]["height"] == 1920
    assert edl["reframes"] and edl["caption_settings"]["preset"] == "hormozi" and edl["caption_settings"]["margin_pct"] == 0.22
    cp = cv2.VideoCapture(str(store.path(jid) / "output" / "final.mp4"))
    assert (int(cp.get(3)), int(cp.get(4))) == (1080, 1920)
    for t in (0.3, 2.5, 5.0):
        cp.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        d = red_detector(cp.read()[1])
        assert d is not None and 0.05 < d[0] < 0.95


def test_new_api_endpoints(job_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    main.store.root = job_dir.parent / "jobs_w7"
    main.store.root.mkdir(exist_ok=True)
    c = TestClient(main.app)
    h = c.get("/api/health").json()
    assert h["ok"] and h["ffmpeg_ok"] and "face_tracking" in h and h["max_upload_mb"] == config.MAX_UPLOAD_MB
    assert {p["key"] for p in c.get("/api/presets").json()["platforms"]} >= {"reels", "youtube"}
    video = (job_dir / "source" / "input.mp4").read_bytes()
    # size limit
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    r = c.post("/api/jobs", files={"file": ("a.mp4", video, "video/mp4")})
    assert r.status_code == 413
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 500)
    # a file that only has a video extension
    before = set(main.store.root.glob("*"))
    r = c.post("/api/jobs", files={"file": ("fake.mp4", b"not a video", "video/mp4")})
    assert r.status_code == 400 and set(main.store.root.glob("*")) == before            # rejected + nothing left behind
    # per-job endpoints (agent runs are stubbed, rendering is covered above)
    jid = main.store.create("x.mp4")
    started = []
    monkeypatch.setattr(main, "_submit", lambda job_id, fn, *a: started.append(a[-1]))
    main.store.update(jid, instruction="make it short remove the fumbling and add captions with calm music and zoom")
    r = c.post(f"/api/jobs/{jid}/chat", json={"message": "zoom kam karo aur caption chhote karo"}).json()
    assert "subtle" in r["reply"] or "kam" in r["reply"]
    assert director.parse_rules(started[-1]).zoom_strength == "subtle" and director.parse_rules(started[-1]).caption_size == "small"
    assert len(main.store.load(jid)["chat"]) == 2
    assert c.post(f"/api/jobs/{jid}/chat", json={"message": "  "}).status_code == 400
    assert c.post("/api/jobs/nope/chat", json={"message": "x"}).status_code == 404
    assert c.put(f"/api/jobs/{jid}/caption-style", json={"preset": "hormozi", "size": "large"}).json() == {"preset": "hormozi", "size": "large"}
    assert c.put(f"/api/jobs/{jid}/caption-style", json={"preset": "neon"}).status_code == 400
    assert c.put(f"/api/jobs/{jid}/caption-style", json={"preset": "auto"}).json() == {}
    assert c.get(f"/api/jobs/{jid}/reference").json()["profile"] is None
