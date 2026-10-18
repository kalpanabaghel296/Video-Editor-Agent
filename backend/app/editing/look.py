"""Colour grading, filters and visual effects for the whole video (the "Look").

Built from the Look model into ONE ffmpeg -vf chain by our own code (no user text reaches the shell).
Applied as a separate pass over the cut video, BEFORE captions are burned in, so captions keep their colours."""
import math
from pathlib import Path
from typing import List

from ..models.edl import EDL, Look
from ..utils.ffmpeg import run

PRESETS = {
    "cinematic": ["eq=contrast=1.10:saturation=1.05:gamma=0.97",
                  "colorbalance=rs=-0.05:gs=-0.01:bs=0.06:rm=-0.02:bm=0.03:rh=0.07:gh=0.02:bh=-0.05"],
    "warm":      ["colorbalance=rs=0.08:gs=0.02:bs=-0.09:rm=0.07:gm=0.01:bm=-0.07:rh=0.05:bh=-0.05", "eq=saturation=1.06"],
    "cool":      ["colorbalance=rs=-0.07:bs=0.09:rm=-0.05:bm=0.07:rh=-0.03:bh=0.05"],
    "bw":        ["hue=s=0", "eq=contrast=1.10"],
    "noir":      ["hue=s=0", "eq=contrast=1.40:brightness=-0.05", "vignette=angle=PI/6"],
    "vintage":   ["curves=preset=vintage", "eq=saturation=0.88:contrast=0.97"],
    "vivid":     ["eq=saturation=1.45:contrast=1.08:gamma=0.98"],
    "faded":     ["eq=contrast=0.88:saturation=0.82", "curves=m='0/0.09 1/0.95'"],
}


def look_filter(look: Look, total: float) -> str:
    """-> comma-separated ffmpeg video filter chain ('' when the look does nothing)"""
    f: List[str] = []
    if look.mirror:
        f.append("hflip")
    f += PRESETS.get(look.preset, [])
    if look.brightness or look.contrast or look.saturation:
        b = look.brightness / 100 * 0.35                      # eq brightness is additive  (-1..1)
        c = 1 + look.contrast / 100 * 0.55                    # 0.45 .. 1.55
        s = 1 + look.saturation / 100                         # 0 .. 2
        f.append(f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}")
    if look.warmth:
        w = look.warmth / 100
        f.append(f"colorbalance=rs={0.16 * w:.3f}:bs={-0.16 * w:.3f}:rm={0.10 * w:.3f}:bm={-0.10 * w:.3f}:rh={0.06 * w:.3f}:bh={-0.06 * w:.3f}")
    if look.sharpen:
        f.append(f"unsharp=5:5:{look.sharpen / 100 * 1.6:.2f}:5:5:0.0")
    if look.vignette:
        # ffmpeg: BIGGER angle = stronger dark corners. 0 -> PI/24 (barely visible) ... 100 -> PI/4.5 (heavy)
        k = 1 / 24 + (1 / 4.5 - 1 / 24) * look.vignette / 100
        f.append(f"vignette=angle=PI*{k:.4f}")
    if look.grain:
        f.append(f"noise=alls={look.grain / 100 * 22:.1f}:allf=t")
    if look.letterbox:
        f.append("drawbox=x=0:y=0:w=iw:h=ih*0.11:color=black:t=fill")
        f.append("drawbox=x=0:y=ih*0.89:w=iw:h=ih*0.11:color=black:t=fill")
    if look.fade_in_out and total > 1.2:
        d = min(0.7, total / 4)
        f.append(f"fade=t=in:st=0:d={d:.2f}")
        f.append(f"fade=t=out:st={max(total - d, 0):.3f}:d={d:.2f}")
    return ",".join(f)


def apply_look(src: Path, dst: Path, edl: EDL) -> Path:
    vf = look_filter(edl.project.look, edl.timeline_duration())
    if not vf:
        return src
    run(["ffmpeg", "-y", "-hide_banner", "-i", str(src), "-vf", vf + ",format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", str(dst)])
    return dst