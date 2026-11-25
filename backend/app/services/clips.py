"""Multi-clip projects: a media bin of clips (order, trim, delete) that is merged into ONE source video.

Everything downstream (transcript, silence / retake detection, planner, captions, renderer) keeps working on a single
`source/input.mp4`, so every existing feature works on the merged timeline without changes.

jobs/<id>/clips/<clip_id>.<ext>   original uploads (never modified)
jobs/<id>/clips.json              manifest: order + trim of every clip
jobs/<id>/source/input.mp4        the merged video (rebuilt after every change)
"""
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..analysis.metadata import extract_metadata
from ..utils.ffmpeg import run

MAX_CLIPS = 30
MIN_LEN = 0.3


def _man_file(jd: Path) -> Path:
    return jd / "clips.json"


def load(jd: Path) -> List[Dict[str, Any]]:
    f = _man_file(jd)
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8")).get("clips", [])


def save(jd: Path, clips: List[Dict[str, Any]]) -> None:
    _man_file(jd).write_text(json.dumps({"clips": clips}, indent=2, ensure_ascii=False), encoding="utf-8")


def clip_dir(jd: Path) -> Path:
    d = jd / "clips"
    d.mkdir(exist_ok=True)
    return d


def new_clip_path(jd: Path, ext: str) -> Path:
    return clip_dir(jd) / f"{uuid.uuid4().hex[:8]}{ext}"


def register(jd: Path, path: Path, name: str) -> Dict[str, Any]:
    """Probe an uploaded clip (already stored in clips/) and add it to the manifest."""
    m = extract_metadata(path)
    entry = {"id": path.stem, "name": name, "file": path.name, "duration": m.duration, "width": m.width,
             "height": m.height, "fps": m.fps, "has_audio": m.has_audio, "trim_start": 0.0, "trim_end": None}
    clips = load(jd)
    clips.append(entry)
    save(jd, clips)
    return entry


def span(c: Dict[str, Any]):
    s = max(float(c.get("trim_start") or 0.0), 0.0)
    e = float(c.get("trim_end") or c["duration"])
    return s, min(max(e, s), c["duration"])


def view(jd: Path) -> Dict[str, Any]:
    out, off = [], 0.0
    for c in load(jd):
        s, e = span(c)
        out.append({"id": c["id"], "name": c["name"], "duration": c["duration"], "width": c["width"], "height": c["height"],
                    "has_audio": c["has_audio"], "trim_start": s, "trim_end": e, "length": round(e - s, 3),
                    "offset": round(off, 3)})
        off += e - s
    return {"clips": out, "total": round(off, 3)}


def update(jd: Path, items: List[Dict[str, Any]]) -> None:
    """items = ordered [{id, trim_start?, trim_end?}]. Clips that are not listed are deleted (their files too)."""
    by_id = {c["id"]: c for c in load(jd)}
    new: List[Dict[str, Any]] = []
    for it in items:
        c = by_id.get(it.get("id"))
        if c is None or any(x["id"] == c["id"] for x in new):
            continue
        c = dict(c)
        ts = float(it.get("trim_start") or 0.0)
        te = it.get("trim_end")
        te = float(te) if te not in (None, "") else None
        ts = min(max(ts, 0.0), max(c["duration"] - MIN_LEN, 0.0))
        if te is not None:
            te = min(te, c["duration"])
            if te - ts < MIN_LEN:
                te = min(ts + MIN_LEN, c["duration"])
            if te >= c["duration"] - 0.01:
                te = None
        c["trim_start"], c["trim_end"] = round(ts, 3), (round(te, 3) if te is not None else None)
        new.append(c)
    if not new:
        raise ValueError("Kam se kam ek clip rehni chahiye (poora project delete karna ho to Delete project use karo).")
    for cid, c in by_id.items():
        if not any(x["id"] == cid for x in new):
            (clip_dir(jd) / c["file"]).unlink(missing_ok=True)
            (clip_dir(jd) / "thumbs" / f"{cid}.jpg").unlink(missing_ok=True)
    save(jd, new)


def thumb(jd: Path, c: Dict[str, Any]) -> Optional[Path]:
    out = clip_dir(jd) / "thumbs" / f"{c['id']}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(exist_ok=True)
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{min(0.5, c['duration'] / 2):.2f}",
             "-i", str(clip_dir(jd) / c["file"]), "-frames:v", "1", "-vf", "scale=160:-2", str(out)])
        return out if out.exists() else None
    except Exception:
        return None


def build_source(jd: Path) -> Path:
    """Merge the clips (in order, with trims) into source/input.mp4. One untouched clip is simply copied."""
    clips = load(jd)
    if not clips:
        raise ValueError("no clips")
    src_dir = jd / "source"
    src_dir.mkdir(exist_ok=True)
    one = len(clips) == 1
    if one and span(clips[0]) == (0.0, clips[0]["duration"]):
        c = clips[0]
        ext = Path(c["file"]).suffix
        for f in src_dir.iterdir():
            f.unlink()
        dest = src_dir / f"input{ext}"
        shutil.copy(clip_dir(jd) / c["file"], dest)
        return dest

    W, H = clips[0]["width"], clips[0]["height"]
    k = min(1.0, 1920 / max(W, H, 1))                                   # never bigger than full HD
    W, H = max(int(W * k) // 2 * 2, 2), max(int(H * k) // 2 * 2, 2)
    fps = int(min(max(round(clips[0].get("fps") or 30), 24), 60))
    any_audio = any(c["has_audio"] for c in clips)
    cmd: List[str] = ["ffmpeg", "-y", "-hide_banner"]
    parts, labels = [], []
    for i, c in enumerate(clips):
        cmd += ["-i", str(clip_dir(jd) / c["file"])]
        s, e = span(c)
        d = e - s
        parts.append(f"[{i}:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,"
                     f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
                     f"setsar=1,fps={fps},format=yuv420p[v{i}]")
        labels.append(f"[v{i}]")
        if any_audio:
            if c["has_audio"]:                                          # apad+atrim: audio is EXACTLY as long as the video part
                parts.append(f"[{i}:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,aresample=44100,"
                             f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={d:.3f}[a{i}]")
            else:                                                       # silent clip between clips with sound
                parts.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={d:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
    n = len(clips)
    parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else ""))
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if any_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart"]
    tmp = jd / "work" / "merged.mp4"
    tmp.parent.mkdir(exist_ok=True)
    run(cmd + [str(tmp)])
    for f in src_dir.iterdir():
        f.unlink()
    dest = src_dir / "input.mp4"
    shutil.move(str(tmp), dest)
    return dest