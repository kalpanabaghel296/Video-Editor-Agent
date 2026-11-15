"""Reference-style matching: "edit my video like THIS reel".

The user uploads a reference video. We measure its *style* with OpenCV + ffmpeg (no model, no internet):
  - aspect ratio / length
  - pace: how often it cuts (average shot length)
  - look: brightness, contrast, saturation, warmth
  - loudness (LUFS)
and the planner then moves the user's own video towards it (see apply_to_requirements):
  aspect -> same format, pace -> clip lengths + punch-in zooms, look -> colour sliders RELATIVE to the user's own
  footage (so a dull recording is lifted exactly as much as the reference is brighter), length -> optional.

What it does NOT detect (honest list): captions/text style, music vs speech, transitions and effects used.
"""
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import config
from ..utils.ffmpeg import ffprobe_json, run

ASPECTS = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}
SAMPLE_FPS = 6.0
DEFAULT_OPTS = {"aspect": True, "pace": True, "look": True, "length": False}


def _aspect_label(w: int, h: int) -> str:
    r = w / max(h, 1)
    return min(ASPECTS, key=lambda k: abs(math.log(r / ASPECTS[k])))


def _frame_stats(frame) -> Dict[str, float]:
    import cv2
    import numpy as np
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype("float32") / 255.0
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    m = frame.reshape(-1, 3).mean(axis=0) / 255.0                      # B, G, R
    return {"brightness": float(g.mean()), "contrast": float(g.std()),
            "saturation": float(hsv[:, :, 1].mean() / 255.0), "warmth": float(m[2] - m[0])}


def _mean(rows: List[Dict[str, float]]) -> Dict[str, float]:
    if not rows:
        return {"brightness": 0.45, "contrast": 0.2, "saturation": 0.3, "warmth": 0.0}
    return {k: round(sum(r[k] for r in rows) / len(rows), 4) for k in rows[0]}


def look_stats(video: Path, max_frames: int = 40) -> Dict[str, float]:
    """Average brightness / contrast / saturation / warmth of a video (frames spread over its length)."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        rows = []
        if n > 0:
            for i in range(max_frames):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * n / max_frames))
                ok, f = cap.read()
                if ok:
                    h, w = f.shape[:2]
                    rows.append(_frame_stats(cv2.resize(f, (160, max(2, int(160 * h / w))))))
        return _mean(rows)
    finally:
        cap.release()


def _loudness(path: Path) -> Optional[float]:
    try:
        err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-t", str(config.REFERENCE_MAX_SECONDS),
                   "-af", "ebur128", "-f", "null", "-"]).stderr
        v = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", err)
        x = float(v[-1]) if v else None
        return x if x is not None and x > -70 else None
    except Exception:
        return None


def analyze(path: Path) -> dict:
    """Style profile of a reference video (json-serialisable)."""
    import cv2
    info = ffprobe_json(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise ValueError("Reference file mein video stream nahi mili")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    duration = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("Reference video khul nahi payi")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))   # OpenCV applies rotation metadata
    step = max(1, int(round(fps / SAMPLE_FPS)))
    limit = min(duration or 1e9, config.REFERENCE_MAX_SECONDS)
    cut_times: List[float] = []
    looks, motions = [], []
    prev_h = prev_g = None
    n = 0
    try:
        while True:
            if not cap.grab():
                break
            if n % step == 0:
                t = n / fps
                if t > limit:
                    break
                ok, f = cap.retrieve()
                if ok:
                    h, w = f.shape[:2]
                    s = cv2.resize(f, (160, max(2, int(160 * h / w))))
                    hsv = cv2.cvtColor(s, cv2.COLOR_BGR2HSV)
                    hist = cv2.calcHist([hsv], [0, 1], None, [16, 4], [0, 180, 0, 256])
                    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
                    g = cv2.cvtColor(s, cv2.COLOR_BGR2GRAY).astype("float32")
                    if prev_h is not None:
                        dh = cv2.compareHist(prev_h, hist, cv2.HISTCMP_BHATTACHARYYA)
                        mad = float(abs(g - prev_g).mean())
                        if dh > 0.38 or mad > 45:
                            if not cut_times or t - cut_times[-1] > 0.4:      # flicker guard
                                cut_times.append(round(t, 2))
                        else:
                            motions.append(mad / 255.0)
                    if len(looks) < 120 and (n // step) % 6 == 0:
                        looks.append(_frame_stats(s))
                    prev_h, prev_g = hist, g
            n += 1
    finally:
        cap.release()
    analysed = min(limit, duration) if duration else limit
    cuts = len(cut_times)
    avg_shot = round(analysed / (cuts + 1), 2) if analysed else 0.0
    return {
        "duration": round(duration, 2), "width": W, "height": H, "fps": round(fps, 2),
        "aspect_ratio": _aspect_label(W, H), "has_audio": has_audio, "lufs": _loudness(path) if has_audio else None,
        "cuts": cuts, "cuts_per_min": round(cuts / max(analysed, 1e-6) * 60, 1), "avg_shot_len": avg_shot,
        "motion": round(sum(motions) / len(motions), 4) if motions else 0.0,
        "look": _mean(looks), "analysed_seconds": round(analysed, 1),
    }


def describe(p: dict) -> List[str]:
    """Plain-language summary for the UI."""
    pace = "fast" if p["avg_shot_len"] < 2.5 else "calm" if p["avg_shot_len"] > 5.5 else "medium"
    lk = p["look"]
    tone = []
    tone.append("bright" if lk["brightness"] > 0.55 else "dark" if lk["brightness"] < 0.35 else "balanced light")
    tone.append("punchy contrast" if lk["contrast"] > 0.26 else "soft contrast" if lk["contrast"] < 0.15 else "normal contrast")
    tone.append("vivid colours" if lk["saturation"] > 0.4 else "muted colours" if lk["saturation"] < 0.2 else "natural colours")
    tone.append("warm tone" if lk["warmth"] > 0.04 else "cool tone" if lk["warmth"] < -0.04 else "neutral tone")
    out = [f"{p['aspect_ratio']} · {p['duration']:.0f}s · {p['width']}x{p['height']}",
           f"Pace: {pace} — a cut every {p['avg_shot_len']:.1f}s ({p['cuts_per_min']:.0f} cuts/min)",
           "Look: " + ", ".join(tone)]
    if p.get("lufs") is not None:
        out.append(f"Loudness: {p['lufs']:.0f} LUFS")
    return out


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def look_sliders(ref: Dict[str, float], src: Dict[str, float]) -> Dict[str, float]:
    """Look sliders (-100..100) that move the SOURCE's look towards the REFERENCE's (damped to 70%, capped)."""
    out = {
        "brightness": _clamp((ref["brightness"] - src["brightness"]) / 0.35 * 100 * 0.7, -40, 40),
        "contrast": _clamp((max(ref["contrast"], 0.02) / max(src["contrast"], 0.02) - 1) / 0.55 * 100 * 0.7, -50, 50),
        "saturation": _clamp((max(ref["saturation"], 0.02) / max(src["saturation"], 0.02) - 1) * 100 * 0.7, -50, 50),
        "warmth": _clamp((ref["warmth"] - src["warmth"]) * 500 * 0.7, -40, 40),
    }
    return {k: (round(v) if abs(v) >= 5 else 0.0) for k, v in out.items()}


def apply_to_requirements(req, profile: dict, src_look: Optional[Dict[str, float]], opts: Optional[dict],
                          src_duration: float) -> List[str]:
    """Move the Requirements towards the reference. Never overrides something the prompt explicitly asked for."""
    o = {**DEFAULT_OPTS, **(opts or {})}
    notes: List[str] = []
    if o["aspect"] and req.aspect_ratio == "original" and profile["aspect_ratio"] != "original":
        req.aspect_ratio = profile["aspect_ratio"]
        notes.append(f"format {profile['aspect_ratio']} (same as the reference)")
    if o["pace"] and profile["avg_shot_len"] > 0:
        shot = _clamp(profile["avg_shot_len"], 1.2, 8.0)
        req.shot_len = round(shot, 2)
        if req.style == "neutral":
            req.style = "fast" if shot < 2.5 else "calm" if shot > 5.5 else "neutral"
        if shot < 4.0:
            req.zoom = True                                       # frequent cuts -> punch-in zooms land on them
        notes.append(f"pace: a cut about every {shot:.1f}s (reference: every {profile['avg_shot_len']:.1f}s)")
    if o["length"] and req.target_duration is None and 0 < profile["duration"] < src_duration * 0.95 \
            and req.target_start is None:
        req.target_duration = float(round(profile["duration"]))
        req.select_mode, req.asks_edit, req.preserve_timeline = "best", True, False
        notes.append(f"length ~{req.target_duration:.0f}s (same as the reference)")
    if o["look"] and src_look:
        sl = look_sliders(profile["look"], src_look)
        base = type(req.look)()
        changed = []
        for k, v in sl.items():
            if v and getattr(req.look, k) == getattr(base, k):    # prompt wins
                setattr(req.look, k, float(v))
                changed.append(f"{k} {v:+.0f}")
        if changed:
            notes.append("look moved towards the reference: " + ", ".join(changed))
            req.color_grade = False
    return notes
