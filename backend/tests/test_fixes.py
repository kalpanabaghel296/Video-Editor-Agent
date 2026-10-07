"""Regression tests: exact trim length, noise removal keeps every frame, music audible, agent brain, audio check."""
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from conftest import TRANSCRIPT
from app.agents import director, loop, planner
from app.analysis.fumbles import detect_fumbles
from app.analysis.pipeline import load_analysis, run_observe
from app.editing import denoise as dn
from app.editing.music import make_track
from app.models.analysis import Silence
from app.models.transcript import Segment, Transcript, Word
from app.services.jobs import JobStore
from app.utils.ffmpeg import ffprobe_json


def duration(p: Path) -> float:
    return float(ffprobe_json(p)["format"]["duration"])


def run_job(job_dir: Path, instruction: str, name: str):
    store = JobStore(job_dir.parent / name)
    jid = store.create("x.mp4")
    shutil.copy(job_dir / "source" / "input.mp4", store.path(jid) / "source" / "input.mp4")
    for f in (job_dir / "analysis").glob("*.json"):
        shutil.copy(f, store.path(jid) / "analysis" / f.name)
    loop.run_agent(store, jid, instruction)
    job = store.load(jid)
    assert job["status"] == "done", job.get("error")
    edl = json.loads((store.path(jid) / "edl.json").read_text(encoding="utf-8"))
    return store, jid, edl, store.path(jid) / "output" / "final.mp4"


@pytest.mark.parametrize("instr,target", [("Trim the video to 10 seconds", 10), ("make a 8 second clip", 8),
                                          ("Make a 6 second reel", 6)])
def test_length_is_the_requested_length(job_dir, instr, target):
    _, _, edl, out = run_job(job_dir, instr, "len_" + str(target))
    assert abs(duration(out) - target) <= 0.45, (duration(out), edl["explanation"])


def test_noise_removal_keeps_every_frame(job_dir):
    an_dur = json.loads((job_dir / "analysis" / "metadata.json").read_text())["duration"] if (job_dir / "analysis" / "metadata.json").exists() else None
    _, _, edl, out = run_job(job_dir, "Remove the background noise and disturbance", "dn_keep")
    src = duration(job_dir / "source" / "input.mp4")
    assert abs(duration(out) - src) < 0.2                      # nothing was cut
    assert len(edl["clips"]) == 1 and edl["audio"]["denoise"] is True
    assert any("Kept every frame" in x for x in edl["explanation"])


def _noisy_signal(sr=16000, secs=12):
    rng = np.random.default_rng(1)
    t = np.arange(sr * secs) / sr
    voice = sum(np.sin(2 * np.pi * f * t) * a for f, a in [(180, .5), (360, .3), (540, .2), (900, .1)])
    voice *= (0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)) ** 1.5 * 0.35
    mask = np.ones_like(t)
    mask[(t > 3) & (t < 4)] = 0
    mask[(t > 7) & (t < 8.5)] = 0
    voice *= mask
    w = rng.standard_normal(len(t))
    pink = np.cumsum(w)
    pink -= np.convolve(pink, np.ones(400) / 400, "same")
    pink /= np.abs(pink).max()                                  # hiss + rumble + 50 Hz mains hum, like a fan / AC
    noise = 0.05 * pink + 0.02 * w + 0.03 * np.sin(2 * np.pi * 50 * t) + 0.015 * np.sin(2 * np.pi * 150 * t)
    return (voice + noise).astype(np.float32), voice.astype(np.float32), sr


@pytest.mark.parametrize("level,min_drop", [("normal", 12), ("strong", 20)])
def test_spectral_denoise_removes_noise_keeps_voice(level, min_drop):
    noisy, voice, sr = _noisy_signal()
    out = dn.denoise_channel(noisy, level)
    assert len(out) == len(noisy)
    gap = lambda x: 20 * np.log10(np.sqrt((x[int(3.2 * sr):int(3.8 * sr)] ** 2).mean()) + 1e-9)
    assert gap(noisy) - gap(out) >= min_drop
    sdr = lambda y: 10 * np.log10((voice ** 2).sum() / (((y - voice) ** 2).sum() + 1e-12))
    assert sdr(out) > sdr(noisy) + 8                           # voice is cleaner, not damaged


def test_builtin_music_tracks(tmp_path):
    for style in ("calm", "neutral", "fast"):
        p = make_track(tmp_path / f"{style}.wav", style)
        with wave.open(str(p)) as w:
            assert w.getnchannels() == 2 and w.getnframes() / w.getframerate() > 5
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        assert np.abs(data).max() > 8000 and np.abs(data[:2000]).max() > 0     # not silent, not clipped to nothing


def test_music_is_clearly_audible_and_ducked(job_dir):
    def wavof(video, out):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(out)], check=True)
        with wave.open(str(out)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float) / 32768
    _, _, e1, o1 = run_job(job_dir, "Add captions to my video", "mus_base")
    _, _, e2, o2 = run_job(job_dir, "Add calm background music to the video", "mus_on")
    assert e2["audio"]["music"] and e2["audio"]["music_style"] == "calm"
    a, b = wavof(o1, job_dir.parent / "a.wav"), wavof(o2, job_dir.parent / "b.wav")
    n = min(len(a), len(b))
    rms = lambda x: 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9)
    assert rms(b[:n] - a[:n]) > -40                            # music contribution is well above the noise floor
    assert rms(b[:n] - a[:n]) < rms(a[:n]) - 3                 # ... but quieter than the speech


def _seg(t0, words):
    ws, t = [], t0
    for w in words:
        ws.append(Word(text=w, start=t, end=t + 0.3))
        t += 0.35
    return Segment(start=t0, end=t, text=" ".join(words), words=ws)


def test_agent_brain_finds_the_mistakes():
    tr = Transcript(duration=40, segments=[
        _seg(0, "so today I want to I want to talk about pricing".split()),
        _seg(5, "the the best tip is simple".split()),
        _seg(12, "this is the first take of my intro".split()),
        _seg(15, "this is the first take of my intro today".split()),
        Segment(start=19, end=19.3, text="thanks", words=[Word(text="thanks", start=19, end=19.3)]),
        Segment(start=22.5, end=23, text="bye", words=[Word(text="bye", start=22.5, end=23)]),
    ])
    kinds = {f.kind for f in detect_fumbles(tr, [Silence(start=30, end=33)], [], 40)}
    assert {"false_start", "stutter", "retake", "long_pause"} <= kinds


def test_brain_cut_is_used_by_planner(job_dir):
    run_observe(job_dir, job_dir / "source" / "input.mp4")
    an = load_analysis(job_dir)
    assert an.fumbles                                          # the test video has long silences
    req = director.parse_rules("make it tight and professional, reel")
    edl = planner.plan(req, an, str(job_dir / "source" / "input.mp4"))
    assert any("Agent brain cut" in x for x in edl.explanation)


def test_prompt_classification():
    r = director.parse_rules("Remove the background noise and disturbance")
    assert r.denoise and r.denoise_level == "strong" and r.preserve_timeline and not r.remove_silence
    r = director.parse_rules("Add background music")
    assert r.music_required and r.preserve_timeline
    r = director.parse_rules("Trim the video to 10 seconds")
    assert r.select_mode == "head" and r.target_duration == 10
    r = director.parse_rules("cut the first 5 seconds")
    assert r.target_start == 5.0 and r.target_duration is None


def test_audio_check_endpoint_and_music_upload(job_dir):
    from fastapi.testclient import TestClient
    from app import main
    main.store.root = job_dir.parent / "jobs_chk"
    main.store.root.mkdir(exist_ok=True)
    c = TestClient(main.app)
    jid = main.store.create("x.mp4")
    shutil.copy(job_dir / "source" / "input.mp4", main.store.path(jid) / "source" / "input.mp4")
    r = c.get(f"/api/jobs/{jid}/audio-check", params={"kind": "source"}).json()
    assert r["has_audio"] and r["verdict"] in ("ok", "very_quiet") and r["codec"] == "aac"
    mp = make_track(job_dir.parent / "u.wav", "calm")
    with open(mp, "rb") as f:
        assert c.post(f"/api/jobs/{jid}/music", files={"file": ("track.wav", f, "audio/wav")}).status_code == 200
    assert list((main.store.path(jid) / "assets").glob("music.wav"))
    assert c.post(f"/api/jobs/{jid}/music", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400