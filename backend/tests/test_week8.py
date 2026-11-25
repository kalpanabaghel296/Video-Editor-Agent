"""Week 8: exact output length, multi-clip projects, delete project, editable captions."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import TRANSCRIPT, make_video
from app.agents import director, loop, planner
from app.analysis.pipeline import load_analysis, run_observe
from app.services.jobs import JobStore
from app.utils.ffmpeg import ffprobe_json


def dur(p: Path) -> float:
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
    return store, jid, job


# ---------------------------------------------------------------- exact length
@pytest.mark.parametrize("target", [5, 8, 11, 13, 14])
def test_output_is_exactly_the_requested_length(job_dir, target):
    """15s source with 4s of pauses + a filler: the old planner returned ~11s for a 14s request."""
    store, jid, job = run_job(job_dir, f"make a {target} second video", f"exact_{target}")
    out = store.path(jid) / "output" / "final.mp4"
    assert abs(dur(out) - target) <= 0.15, (dur(out), job["explanation"])


def test_exact_length_with_captions_music_zoom(job_dir):
    store, jid, job = run_job(job_dir, "make a 12 sec vertical video with captions, zoom and calm music", "exact_full")
    assert abs(dur(store.path(jid) / "output" / "final.mp4") - 12) <= 0.15


def test_plan_matches_target_when_cuts_would_make_it_short(job_dir):
    run_observe(job_dir, job_dir / "source" / "input.mp4")
    an = load_analysis(job_dir)
    req = director.understand("make a 14 sec video", an.metadata)
    edl = planner.plan(req, an, str(job_dir / "source" / "input.mp4"))
    assert abs(edl.timeline_duration() - 14) <= 0.05
    assert not edl.warnings


def test_too_short_source_warns_instead_of_lying(job_dir):
    run_observe(job_dir, job_dir / "source" / "input.mp4")
    an = load_analysis(job_dir)
    req = director.understand("make a 40 sec video", an.metadata)
    edl = planner.plan(req, an, str(job_dir / "source" / "input.mp4"))
    assert edl.timeline_duration() <= an.metadata.duration + 0.01
    assert edl.warnings and "40" in edl.warnings[0]


# ---------------------------------------------------------------- multi clip / delete
def wait_idle(c, jid, limit=180):
    for _ in range(limit):
        s = c.get(f"/api/jobs/{jid}").json()
        if not s["busy"] and s["status"] in ("done", "failed", "analyzed", "uploaded"):
            return s
        time.sleep(1)
    raise AssertionError("timeout")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "store", JobStore(tmp_path / "jobs"))
    return TestClient(main.app)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    a = make_video(d / "a.mp4")                                  # 640x360, 15s, audio
    b = d / "b.mp4"                                              # 360x640 portrait, 4s, NO audio
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(b)], check=True)
    c = d / "c.mp4"                                              # 480x270, 3s, audio
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=480x270:rate=24:duration=3",
                    "-f", "lavfi", "-i", "sine=f=330:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(c)],
                   check=True)
    return a, b, c


def up(path: Path):
    return ("file", (path.name, path.read_bytes(), "video/mp4"))


def test_multi_clip_project_merge_reorder_trim_delete(client, clips):
    a, b, c = clips
    r = client.post("/api/jobs", files=[up(a), up(b)], data={"defer": "true"})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    wait_idle(client, jid)
    v = client.get(f"/api/jobs/{jid}/clips").json()
    assert [x["name"] for x in v["clips"]] == ["a.mp4", "b.mp4"]
    src = client.get(f"/api/jobs/{jid}/video?kind=source")
    assert src.status_code == 200
    merged = Path(main_store(client).path(jid) / "source" / "input.mp4")
    assert abs(dur(merged) - 19.0) < 0.3                         # 15 + 4
    info = ffprobe_json(merged)
    assert any(s["codec_type"] == "audio" for s in info["streams"])   # silent clip got silence, audio is kept

    assert client.post(f"/api/jobs/{jid}/clips", files=[up(c)]).status_code == 200
    wait_idle(client, jid)
    assert abs(dur(merged) - 22.0) < 0.3                         # + 3

    ids = [x["id"] for x in client.get(f"/api/jobs/{jid}/clips").json()["clips"]]
    new = [{"id": ids[2]}, {"id": ids[0], "trim_start": 2.0, "trim_end": 7.0}]      # reorder, trim, delete the middle one
    assert client.put(f"/api/jobs/{jid}/clips", json={"clips": new}).status_code == 200
    wait_idle(client, jid)
    v = client.get(f"/api/jobs/{jid}/clips").json()
    assert [x["id"] for x in v["clips"]] == [ids[2], ids[0]]
    assert abs(v["total"] - 8.0) < 0.01 and abs(dur(merged) - 8.0) < 0.3
    assert not (main_store(client).path(jid) / "clips" / next(Path(main_store(client).path(jid) / "clips").glob(f"{ids[1]}*")).name).exists() \
        if list((main_store(client).path(jid) / "clips").glob(f"{ids[1]}*")) else True
    assert client.get(f"/api/jobs/{jid}/clips/{ids[0]}/thumb").status_code == 200
    assert client.put(f"/api/jobs/{jid}/clips", json={"clips": []}).status_code == 400   # cannot delete the last clip


def main_store(client):
    from app import main
    return main.store


def test_edit_runs_on_the_merged_timeline_with_exact_length(client, clips):
    a, b, c = clips
    r = client.post("/api/jobs", files=[up(a), up(c)], data={"defer": "true"})
    jid = r.json()["id"]
    wait_idle(client, jid)
    r = client.post(f"/api/jobs/{jid}/edit", json={"instruction": "make a 10 second video with captions"})
    assert r.status_code == 200
    s = wait_idle(client, jid)
    assert s["status"] == "done", s
    assert abs(dur(main_store(client).path(jid) / "output" / "final.mp4") - 10) <= 0.15


def test_clip_change_resets_old_result(client, clips):
    a, b, c = clips
    jid = client.post("/api/jobs", files=[up(a)], data={"instruction": "make a 6 second video"}).json()["id"]
    assert wait_idle(client, jid)["status"] == "done"
    client.post(f"/api/jobs/{jid}/clips", files=[up(c)])
    s = wait_idle(client, jid)
    assert s["status"] == "uploaded" and not s["outputs"]
    assert not (main_store(client).path(jid) / "output" / "final.mp4").exists()


def test_single_clip_old_api_still_works(client, clips):
    a, b, c = clips
    r = client.post("/api/jobs", files={"file": ("a.mp4", a.read_bytes(), "video/mp4")}, data={"defer": "true"})
    assert r.status_code == 200
    jid = r.json()["id"]
    assert (main_store(client).path(jid) / "source" / "input.mp4").exists()
    assert client.post("/api/jobs", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400


def test_delete_project(client, clips):
    a, b, c = clips
    jid = client.post("/api/jobs", files=[up(a)], data={"defer": "true"}).json()["id"]
    path = main_store(client).path(jid)
    assert path.exists()
    assert client.delete(f"/api/jobs/{jid}").status_code == 200
    assert not path.exists()
    assert client.get(f"/api/jobs/{jid}").status_code == 404
    assert all(j["id"] != jid for j in client.get("/api/jobs").json())
    assert client.delete(f"/api/jobs/{jid}").status_code == 404


# ---------------------------------------------------------------- editable captions
def _done_job_with_captions(client, clips, instr="make a 10 second video with captions"):
    a, _, _ = clips
    jid = client.post("/api/jobs", files=[up(a)], data={"defer": "true"}).json()["id"]
    wait_idle(client, jid)
    st = main_store(client)
    # stand-in for Whisper: put the hand-made transcript in place
    (st.path(jid) / "analysis").mkdir(exist_ok=True)
    (st.path(jid) / "analysis" / "transcript.json").write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
    client.post(f"/api/jobs/{jid}/edit", json={"instruction": instr})
    assert wait_idle(client, jid)["status"] == "done"
    return jid


def test_edit_captions_changes_text_not_length_and_skips_base_render(client, clips):
    jid = _done_job_with_captions(client, clips)
    st = main_store(client)
    caps = client.get(f"/api/jobs/{jid}/captions?kind=final&format=json").json()
    assert caps
    before = dur(st.path(jid) / "output" / "final.mp4")
    base = st.path(jid) / "work" / "base.mp4"
    m0 = base.stat().st_mtime_ns
    lines = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in caps]
    lines[0]["text"] = "HELLO FROM MANUAL EDIT"
    lines.append({"start": 0.2, "end": 0.9, "text": "extra line"}) if False else None
    r = client.put(f"/api/jobs/{jid}/captions/edit", json={"captions": lines})
    assert r.status_code == 200, r.text
    s = wait_idle(client, jid)
    assert s["status"] == "done", s
    assert abs(dur(st.path(jid) / "output" / "final.mp4") - before) <= 0.1
    assert base.stat().st_mtime_ns == m0                         # slow first pass was NOT repeated
    srt = (st.path(jid) / "output" / "final.srt").read_text(encoding="utf-8")
    assert "HELLO FROM MANUAL EDIT" in srt


def test_add_and_delete_caption_lines(client, clips):
    jid = _done_job_with_captions(client, clips)
    st = main_store(client)
    caps = client.get(f"/api/jobs/{jid}/captions?kind=final&format=json").json()
    keep = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in caps[1:]]     # delete the first line
    keep.append({"start": 0.1, "end": 0.9, "text": "brand new line"})                         # add one
    client.put(f"/api/jobs/{jid}/captions/edit", json={"captions": keep})
    assert wait_idle(client, jid)["status"] == "done"
    now = client.get(f"/api/jobs/{jid}/captions?kind=final&format=json").json()
    texts = [c["text"] for c in now]
    assert "brand new line" in texts and caps[0]["text"] not in texts
    assert now == sorted(now, key=lambda c: c["start"])
    # all lines removed -> video without captions, still valid
    client.put(f"/api/jobs/{jid}/captions/edit", json={"captions": []})
    assert wait_idle(client, jid)["status"] == "done"


def test_manual_captions_survive_a_rerun_with_same_clips(client, clips):
    jid = _done_job_with_captions(client, clips)
    caps = client.get(f"/api/jobs/{jid}/captions?kind=final&format=json").json()
    lines = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in caps]
    lines[0]["text"] = "kept after rerun"
    client.put(f"/api/jobs/{jid}/captions/edit", json={"captions": lines})
    wait_idle(client, jid)
    client.post(f"/api/jobs/{jid}/edit", json={"instruction": "make a 10 second video with captions"})
    assert wait_idle(client, jid)["status"] == "done"
    now = client.get(f"/api/jobs/{jid}/captions?kind=final&format=json").json()
    assert "kept after rerun" in [c["text"] for c in now]


def test_regenerate_captions_from_speech(client, clips):
    jid = _done_job_with_captions(client, clips)
    client.put(f"/api/jobs/{jid}/captions/edit", json={"captions": [{"start": 0.1, "end": 1.0, "text": "zzz"}]})
    wait_idle(client, jid)
    assert client.post(f"/api/jobs/{jid}/captions/regenerate").status_code == 200
    assert wait_idle(client, jid)["status"] == "done"
    now = client.get(f"/api/jobs/{jid}/captions?kind=final&format=json").json()
    assert now and "zzz" not in [c["text"] for c in now]


def test_caption_edit_needs_a_finished_edit(client, clips):
    a, _, _ = clips
    jid = client.post("/api/jobs", files=[up(a)], data={"defer": "true"}).json()["id"]
    assert client.put(f"/api/jobs/{jid}/captions/edit", json={"captions": []}).status_code == 404