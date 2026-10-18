"""Look & Effects: every control must visibly change the pixels (measured), prompt parsing, UI merge, API."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import make_video
from app.agents import director, loop
from app.editing.look import look_filter
from app.models.edl import Look
from app.services.jobs import JobStore
from app.utils.ffmpeg import ffprobe_json


@pytest.fixture(scope="module")
def colorful(tmp_path_factory):
    p = tmp_path_factory.mktemp("look") / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=4:r=25",
                    "-f", "lavfi", "-i", "sine=f=300:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


def frame(video: Path, look: Look, t=1.5, total=4.0):
    vf = (look_filter(look, total) or "null") + ",scale=160:120"
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", vf, "-ss", str(t), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(120, 160, 3).astype(float)


def stats(a):
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    mx, mn = a.max(2), a.min(2)
    return dict(luma=lum.mean(), sat=((mx - mn) / (mx + 1e-6)).mean(), rb=(a[..., 0] - a[..., 2]).mean(),
                corner=lum[:12, :16].mean(), top=lum[:6].mean())


def test_default_look_is_noop():
    assert Look().is_default and look_filter(Look(), 10) == ""


def test_brightness_contrast_saturation(colorful):
    base = stats(frame(colorful, Look()))
    assert stats(frame(colorful, Look(brightness=60)))["luma"] > base["luma"] + 25
    assert stats(frame(colorful, Look(brightness=-60)))["luma"] < base["luma"] - 25
    assert stats(frame(colorful, Look(saturation=-100)))["sat"] < 0.12 < base["sat"]
    assert stats(frame(colorful, Look(saturation=80)))["sat"] > base["sat"]


def test_warmth_and_presets(colorful):
    base = stats(frame(colorful, Look()))
    assert stats(frame(colorful, Look(warmth=80)))["rb"] > base["rb"] + 10
    assert stats(frame(colorful, Look(warmth=-80)))["rb"] < base["rb"] - 10
    assert stats(frame(colorful, Look(preset="bw")))["sat"] < 0.12
    assert stats(frame(colorful, Look(preset="vivid")))["sat"] > base["sat"]
    assert stats(frame(colorful, Look(preset="faded")))["sat"] < base["sat"]
    for pr in ("cinematic", "warm", "cool", "noir", "vintage"):          # every preset renders without ffmpeg errors
        frame(colorful, Look(preset=pr))


def test_vignette_letterbox_fade_mirror(colorful):
    base = stats(frame(colorful, Look()))
    assert stats(frame(colorful, Look(vignette=100)))["corner"] < base["corner"] * 0.7
    assert stats(frame(colorful, Look(letterbox=True)))["top"] < 5
    assert stats(frame(colorful, Look(fade_in_out=True), t=0.0))["luma"] < 10 < base["luma"]
    a, m = frame(colorful, Look()), frame(colorful, Look(mirror=True))
    assert np.abs(m[:, ::-1] - a).mean() < np.abs(m - a).mean()
    frame(colorful, Look(sharpen=100)); frame(colorful, Look(grain=100))


def test_prompt_parsing():
    P = director.parse_look
    assert P("make it black and white").preset == "bw"
    assert P("add a vintage look with film grain").preset == "vintage" and P("film grain").grain > 0
    assert P("make it brighter").brightness > 0 and P("make it darker").brightness < 0
    assert P("brightness +20 and contrast -15").brightness == 20 and P("brightness +20 and contrast -15").contrast == -15
    assert P("reduce brightness by 25").brightness == -25
    k = P("cinematic letterbox, vignette, fade in and out, mirror, sharpen")
    assert k.preset == "cinematic" and k.letterbox and k.vignette and k.fade_in_out and k.mirror and k.sharpen
    assert P("just trim it").is_default
    r = director.parse_rules("Make it black and white")
    assert r.preserve_timeline and not r.remove_silence                    # styling never cuts frames


def run_job(job_dir, instr, name, look=None):
    store = JobStore(job_dir.parent / name)
    jid = store.create("x.mp4")
    shutil.copy(job_dir / "source" / "input.mp4", store.path(jid) / "source" / "input.mp4")
    for f in (job_dir / "analysis").glob("*.json"):
        shutil.copy(f, store.path(jid) / "analysis" / f.name)
    if look is not None:                                                    # what the UI does
        (store.path(jid) / "assets").mkdir(exist_ok=True)
        (store.path(jid) / "assets" / "look.json").write_text(look.model_dump_json(), encoding="utf-8")
    loop.run_agent(store, jid, instr)
    job = store.load(jid)
    assert job["status"] == "done", job.get("error")
    return store.path(jid) / "output" / "final.mp4", json.loads((store.path(jid) / "edl.json").read_text(encoding="utf-8"))


def mid_frame_stats(video):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", "scale=160:120", "-ss", "1.0", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    return stats(np.frombuffer(raw, np.uint8).reshape(120, 160, 3).astype(float))


def dur(p):
    return float(ffprobe_json(p)["format"]["duration"])


def test_prompt_look_end_to_end_keeps_all_frames(job_dir):
    base, _ = run_job(job_dir, "Add captions to my video", "lk_base")
    out, edl = run_job(job_dir, "Make it black and white", "lk_bw")
    assert mid_frame_stats(out)["sat"] < 0.12 < mid_frame_stats(base)["sat"] + 0.5
    assert abs(dur(out) - dur(job_dir / "source" / "input.mp4")) < 0.2
    assert edl["project"]["look"]["preset"] == "bw"


def test_ui_look_merges_over_prompt_and_only_styling_drops_nothing(job_dir):
    ui = Look(brightness=60, letterbox=True)
    out, edl = run_job(job_dir, "Apply my selected look and effects. Keep the full video.", "lk_ui", look=ui)
    assert edl["project"]["look"]["brightness"] == 60 and edl["project"]["look"]["letterbox"]
    assert abs(dur(out) - dur(job_dir / "source" / "input.mp4")) < 0.2
    assert any("Look & effects applied" in x for x in edl["explanation"])
    # UI value wins over the prompt's own look
    out2, edl2 = run_job(job_dir, "make it black and white", "lk_ui2", look=Look(preset="vivid"))
    assert edl2["project"]["look"]["preset"] == "vivid"


def test_look_api(job_dir):
    from fastapi.testclient import TestClient
    from app import main
    main.store.root = job_dir.parent / "jobs_look_api"
    main.store.root.mkdir(exist_ok=True)
    c = TestClient(main.app)
    jid = main.store.create("x.mp4")
    assert c.get(f"/api/jobs/{jid}/look").json()["preset"] == "none"
    r = c.put(f"/api/jobs/{jid}/look", json={"preset": "noir", "brightness": 15, "mirror": True})
    assert r.status_code == 200 and c.get(f"/api/jobs/{jid}/look").json()["preset"] == "noir"
    assert c.put(f"/api/jobs/{jid}/look", json={"brightness": 500}).status_code == 422      # out of range rejected
    assert c.put(f"/api/jobs/{jid}/look", json={"preset": "bogus"}).status_code == 422