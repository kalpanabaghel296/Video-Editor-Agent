"""PLAN stage: Requirements + Analysis -> EDL.

Scores transcript segments (hooks, substance, keywords, filler ratio), selects the best
ones within the target duration, cuts silences / filler words inside them, then writes
clips, captions, effects, transitions and a human-readable explanation into the EDL.
"""
import json
import re
from typing import List, Optional, Tuple
from pathlib import Path
from ..analysis import reference as reference_mod
from ..analysis.fumbles import detect_fumbles
from ..editing import reframe as reframe_mod
from ..models.edl import Look
from ..editing.captions import build_captions, script_to_captions
from ..models.analysis import AnalysisBundle
from ..models.edl import (EDL, AudioSettings, CaptionSettings, Clip, Effect, Overlay, Reframe,
                          ProjectSettings, SfxEvent, SourceMedia, Transition)
from ..models.transcript import Segment
from .director import Requirements

HOOK_WORDS = {"how", "why", "secret", "important", "key", "never", "always", "best", "biggest", "mistake",
              "tip", "tips", "trick", "first", "finally", "remember", "imagine", "new", "free", "need",
              "must", "learn", "today", "problem", "solution", "result"}
Interval = Tuple[float, float]

SILENCE_CUT_MIN = 0.6      # only cut silences at least this long
SILENCE_KEEP = 0.12        # keep this much natural pause on each side
MIN_CLIP = 0.4
MERGE_GAP = 0.25
PAD_BEFORE, PAD_AFTER = 0.08, 0.18


def resolution_for(aspect: str, w: int, h: int, req: Optional[Requirements] = None) -> Tuple[int, int]:
    if req and req.target_width and req.target_height:
        return (req.target_width // 2 * 2, req.target_height // 2 * 2)
    table = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}
    if aspect in table:
        return table[aspect]
    return (w // 2 * 2 or 2, h // 2 * 2 or 2)


def subtract(base: Interval, cuts: List[Interval]) -> List[Interval]:
    out, cur = [], base[0]
    for s, e in sorted(cuts):
        if e <= cur or s >= base[1]:
            continue
        if s > cur:
            out.append((cur, min(s, base[1])))
        cur = max(cur, e)
    if cur < base[1]:
        out.append((cur, base[1]))
    return out


def merge(ivs: List[Interval], gap: float = MERGE_GAP) -> List[Interval]:
    ivs = sorted(ivs)
    out: List[Interval] = []
    for s, e in ivs:
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def score_segment(seg: Segment, idx: int, n: int, keywords: List[str], filler_ratio: float,
                  cut_bloopers: bool = False) -> float:
    words = re.findall(r"[\w']+", seg.text.lower())
    if not words:
        return 0.0
    dur = max(seg.end - seg.start, 0.1)
    s = min(len(words), 25) / 25 * 0.35 + min(len(words) / dur, 3.5) / 3.5 * 0.15
    s += 0.2 if any(w in HOOK_WORDS for w in words) else 0
    s += 0.1 if ("?" in seg.text or any(ch.isdigit() for ch in seg.text)) else 0
    if keywords:
        hits = sum(1 for k in keywords if k in words)
        s += 0.45 * min(hits, 3) / 3
    s -= 0.4 * filler_ratio
    if cut_bloopers:
        if idx == 0 and n > 1:
            s -= 0.35
        elif idx > 0:
            s += 0.1
    else:
        s += 0.15 if idx == 0 else (0.05 if idx == n - 1 else 0)
    return round(max(0.0, min(s, 2.0)), 3)


def _total(ivs: List[Interval]) -> float:
    return sum(e - s for s, e in ivs)


def _take(ivs: List[Interval], amount: float, from_start: bool = True) -> List[Interval]:
    """take `amount` seconds from the start (or end) of a list of intervals"""
    out, left = [], amount
    for s_, e_ in (ivs if from_start else list(reversed(ivs))):
        if left <= 1e-3:
            break
        d = min(e_ - s_, left)
        out.append((s_, s_ + d) if from_start else (e_ - d, e_))
        left -= d
    return sorted(out)


def _extend_to(ivs: List[Interval], free: List[Interval], target: float) -> List[Interval]:
    """grow the selection to `target` seconds using free material right next to it (natural continuation)"""
    ivs = sorted(ivs)
    for _ in range(50):
        gap = target - _total(ivs)
        if gap <= 0.05 or not free:
            break
        end_edge = ivs[-1][1] if ivs else 0.0
        after = sorted((f for f in free if f[1] > end_edge), key=lambda f: f[0])
        before = sorted((f for f in free if f[0] < (ivs[0][0] if ivs else 0.0)), key=lambda f: -f[1])
        if after:
            f = after[0]
            s_, e_ = max(f[0], end_edge), f[1]
            take = min(e_ - s_, gap)
            ivs.append((s_, s_ + take))
        elif before:
            f = before[0]
            start_edge = ivs[0][0]
            s_, e_ = f[0], min(f[1], start_edge)
            take = min(e_ - s_, gap)
            ivs.insert(0, (e_ - take, e_))
        else:
            break
        ivs = merge(sorted(ivs), 0.0)
        free = [x for iv in subtract_all(free, ivs) for x in [iv]]
    return ivs


def subtract_all(base: List[Interval], cuts: List[Interval]) -> List[Interval]:
    out: List[Interval] = []
    for b_ in base:
        out += subtract(b_, cuts)
    return [x for x in out if x[1] - x[0] > 0.02]


_KIND_RANK = {"long_pause": 0.0, "hesitation": 0.0, "filler": 1.0, "stutter": 2.0, "false_start": 2.0, "retake": 3.0}


def _fill_gaps(ivs: List[Interval], window: Interval, target: float, fumbles) -> List[Interval]:
    """Grow the selection to `target` seconds by giving back material that was cut.
    Order (least harmful first): pauses -> material at the edges -> unused speech -> fillers -> stutters -> re-takes.
    Works on gaps INSIDE the selection too (the old code only grew at the outer edges)."""
    ivs = sorted(ivs)
    need = target - _total(ivs)
    if need <= 0.02:
        return ivs
    first_s = ivs[0][0] if ivs else window[0]
    last_e = ivs[-1][1] if ivs else window[1]
    ranked = []
    for g in subtract_all([window], ivs):
        if g[1] <= first_s + 1e-6:
            rank, kind = 0.4, "lead"
        elif g[0] >= last_e - 1e-6:
            rank, kind = 0.4, "trail"
        else:
            hits = [_KIND_RANK.get(f.kind, 0.5) for f in fumbles if f.start < g[1] - 0.05 and f.end > g[0] + 0.05]
            rank, kind = (max(hits) if hits else 0.5), "mid"
        ranked.append((rank, g[0], g, kind))
    ranked.sort(key=lambda x: (x[0], x[1]))
    out = list(ivs)
    for rank, _, g, kind in ranked:
        if need <= 0.02:
            break
        take = min(g[1] - g[0], need)
        # pauses / trailing material stay attached to the speech BEFORE them, everything else leads into the NEXT clip
        piece = (g[0], g[0] + take) if (kind == "trail" or (kind == "mid" and rank < 0.45)) else (g[1] - take, g[1])
        out.append(piece)
        need -= take
    return merge(sorted(out), 0.0)


def _brain_cuts(req: Requirements, an: AnalysisBundle, win: Interval, source_path: Optional[str] = None):
    """Turn the brain's findings (analysis/fumbles.py) into cut intervals according to what the prompt allows."""
    audio = Path(source_path) if (source_path and an.metadata.has_audio) else None
    cache = Path(source_path).parent.parent / "analysis" / "take_energy.json" if source_path else None
    fum = detect_fumbles(an.transcript, an.silences, an.fillers, an.metadata.duration, aggressive=True,
                         audio_path=audio, energy_cache=cache) \
        if req.style == "fast" else an.fumbles
    allowed = set()
    if req.remove_silence:
        allowed |= {"long_pause", "hesitation"}
    if req.remove_fillers:
        allowed |= {"filler"}
    if req.smart_cuts:
        allowed |= {"stutter", "false_start", "retake"}
    used = [f for f in fum if f.kind in allowed and f.end > win[0] and f.start < win[1]
            and not (f.kind in ("stutter", "false_start", "retake") and f.confidence < 0.55)]
    cuts = [(max(f.start, win[0]), min(f.end, win[1])) for f in used]
    return used, cuts


def _brain_notes(used) -> List[str]:
    if not used:
        return []
    names = {"long_pause": "long pause", "hesitation": "hesitation", "stutter": "stutter", "false_start": "false start",
             "retake": "re-take", "filler": "filler word"}
    by: dict = {}
    for f in used:
        by.setdefault(f.kind, []).append(f)
    head = ", ".join(f"{len(v)} {names[k]}{'s' if len(v) > 1 else ''} ({sum(x.end - x.start for x in v):.1f}s)" for k, v in by.items())
    notes = [f"Agent brain cut: {head}"]
    for f in sorted(used, key=lambda x: -(x.end - x.start))[:6]:
        notes.append(f"  - {f.start:.1f}s-{f.end:.1f}s: {f.reason}")
    return notes


def _user_music(source_path: str) -> Optional[str]:
    """music file the user uploaded for this job (jobs/<id>/assets/music.*), if any"""
    assets = Path(source_path).parent.parent / "assets"
    if assets.exists():
        for f in sorted(assets.glob("music.*")):
            return str(f)
    return None


def _merge_look(req: Requirements, source_path: str) -> Tuple[Look, bool]:
    """prompt look + the sliders / filters chosen in the UI (jobs/<id>/assets/look.json). UI values win.
    -> (look, ui_look_used)"""
    look = req.look.model_copy()
    f = Path(source_path).parent.parent / "assets" / "look.json"
    if not f.exists():
        return look, False
    try:
        ui = Look(**json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        return look, False
    if ui.is_default:
        return look, False
    base = Look()
    for k in Look.model_fields:
        v = getattr(ui, k)
        if v != getattr(base, k):
            setattr(look, k, v)
    return look, True


def _assets(source_path: str) -> Path:
    return Path(source_path).parent.parent / "assets"


def _ui_caption_style(source_path: str) -> dict:
    """caption look chosen in the UI (jobs/<id>/assets/caption_style.json); only non-default choices are stored"""
    f = _assets(source_path) / "caption_style.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def _apply_reference(req: Requirements, source_path: str, dur: float) -> List[str]:
    """reference-video style (jobs/<id>/assets/reference.json, written by POST /api/jobs/<id>/reference)"""
    f = _assets(source_path) / "reference.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        prof, opts = data["profile"], data.get("options", {})
        if not data.get("enabled", True):
            return []
        src_look = None
        if {**reference_mod.DEFAULT_OPTS, **opts}["look"]:
            try:
                src_look = reference_mod.look_stats(Path(source_path))
            except Exception:
                src_look = None
        return reference_mod.apply_to_requirements(req, prof, src_look, opts, dur)
    except Exception:
        return []


ZOOM_LEVEL = {"subtle": 1.06, "normal": 1.12, "strong": 1.22}


def _reframes(edl: EDL, req: Requirements, source_path: str) -> Tuple[List[Reframe], List[str]]:
    """Face-aware crop paths for every clip (only when a wide video is cropped to 9:16 / 1:1)."""
    p = edl.project
    if p.aspect_ratio == "original" or p.fit != "crop" or req.reframe not in ("auto", "face"):
        return [], []
    axis, _ = reframe_mod.crop_geometry(edl.source_media.width, edl.source_media.height, p.width, p.height)
    if axis is None:
        return [], []
    if not reframe_mod.available():
        return [], ["Face tracking unavailable (OpenCV face detector not installed) — centre crop used"]
    out, ratios = [], []
    for c in edl.clips:
        try:
            r = reframe_mod.track_clip(Path(source_path), c.source_start, c.source_end, edl.source_media.width,
                                       edl.source_media.height, p.width, p.height)
        except Exception:
            r = None
        if r:
            out.append(Reframe(clip_id=c.id, axis=r[0], points=r[1], face_ratio=r[2]))
            ratios.append(r[2])
    if out:
        return out, [f"Face-aware framing: followed the speaker in {len(out)} of {len(edl.clips)} clip(s) "
                     f"(face found in {sum(ratios) / len(ratios) * 100:.0f}% of sampled frames)"]
    return [], ["No face found for reframing — centre crop used"]


def plan(req: Requirements, an: AnalysisBundle, source_path: str) -> EDL:
    meta, dur = an.metadata, an.metadata.duration
    req.notes += _apply_reference(req, source_path, dur)             # reference style first: the prompt still wins
    look, ui_look = _merge_look(req, source_path)
    if ui_look and not req.asks_edit and not req.preserve_timeline:
        # only styling was chosen in the UI -> never drop a frame
        req.preserve_timeline = True
        req.remove_silence = req.remove_fillers = req.smart_cuts = False
    win_s = max(req.target_start, 0.0) if req.target_start is not None else 0.0
    win_e = min(req.target_end, dur) if req.target_end is not None else dur
    window: Interval = (win_s, win_e)
    used_fumbles, brain_cuts = _brain_cuts(req, an, window, source_path)
    removed_fillers = sum(1 for f in used_fumbles if f.kind == "filler")
    cuts: List[Interval] = merge(brain_cuts, 0.0)

    # candidate segments (scored by hook words, substance, keywords ...)
    segs = [s for s in an.transcript.segments if s.end > s.start]
    transcript_used = bool(segs)
    cand = []   # (seg_index, score, usable_intervals, text)
    if segs:
        for i, sg in enumerate(segs):
            if sg.start >= win_e or sg.end <= win_s:
                continue
            toks = re.findall(r"[\w']+", sg.text.lower())
            real = [t for t in toks if t not in {"um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "mmm"}]
            if not real:
                continue                                  # pure filler segment
            fr = 1 - len(real) / max(len(toks), 1)
            a, b = max(sg.start - PAD_BEFORE, win_s), min(sg.end + PAD_AFTER, win_e)
            usable = [iv for iv in subtract((a, b), cuts) if iv[1] - iv[0] >= MIN_CLIP]
            if usable:
                cand.append((i, score_segment(sg, i, len(segs), req.keywords, fr, req.cut_bloopers), usable, sg.text.strip()))
    else:                                                 # no transcript -> use the non-cut regions
        for i, iv in enumerate([x for x in subtract(window, cuts) if x[1] - x[0] >= MIN_CLIP]):
            cand.append((i, round(min(iv[1] - iv[0], 10) / 10, 3), [iv], ""))
    if not cand:                                          # nothing usable: fall back to the requested window
        cand = [(0, 0.5, [window], "")]

    free = [x for x in subtract(window, cuts) if x[1] - x[0] >= 0.05]       # everything the brain did NOT cut
    if not free:
        free = [window]
    target = req.target_duration
    total_free = _total(free)
    explicit = req.target_start is not None and req.target_end is not None
    chosen = cand

    if explicit and not cuts:
        # "trim to 0:00-0:15": exactly that window, contiguous, nothing dropped inside it
        ivs = [window]
        chosen = [c for c in cand if any(a < window[1] and b > window[0] for a, b in c[2])] or chosen
    elif not target or (total_free <= target + 0.05 and not req.cut_bloopers):
        # nothing has to be thrown away to hit a length -> keep EVERYTHING except the brain's cuts.
        # (Never rebuild the video from speech segments only: that silently deletes non-speech frames.)
        ivs = merge(free, 0.05)
    elif req.select_mode == "head" and not req.cut_bloopers:
        ivs = _take(free, target, True)                   # "trim to N seconds": the first N seconds of the cleaned video
    else:
        # highlight mode: best segments first ...
        chosen, used_t = [], 0.0
        for c in sorted(cand, key=lambda x: -x[1]):
            d = _total(c[2])
            if used_t + d <= target:
                chosen.append(c)
                used_t += d
        if not chosen:
            chosen = [max(cand, key=lambda x: x[1])]
        # ... then FILL the remaining time with the neighbouring material, so the length really is the target
        idx = {c[0] for c in chosen}
        remaining = target - sum(_total(c[2]) for c in chosen)
        for c in sorted([c for c in cand if c[0] not in idx], key=lambda c: (min(abs(c[0] - i) for i in idx) > 1, -c[1])):
            if remaining <= 0.3:
                break
            d = _total(c[2])
            if d <= remaining + 0.05:
                chosen.append(c)
                remaining -= d
            elif remaining >= 1.0 and (c[0] - 1) in idx:
                # natural continuation of a chosen segment: take the beginning of the next one
                chosen.append((c[0], c[1], _take(c[2], remaining, from_start=True), c[3]))
                remaining = 0
        chosen.sort(key=lambda x: x[0])
        ivs = merge([iv for c in chosen for iv in c[2]])
    length_warning: Optional[str] = None
    if target:
        # EXACT LENGTH: the user asked for `target` seconds -> the result must be `target` seconds (not shorter, not longer)
        if _total(ivs) < target - 0.05:                   # short: continue with free material next to the selection ...
            ivs = _extend_to(ivs, subtract_all(free, ivs), target)
        if _total(ivs) < target - 0.05:                   # ... then give back cut material (pauses first, bad takes last)
            ivs = _fill_gaps(ivs, window, target, used_fumbles)
        have = _total(ivs)
        if have < target - 0.1:
            length_warning = (f"Source mein sirf {have:.1f}s material hai, isliye {target:.0f}s ka video nahi ban sakta - "
                              f"{have:.1f}s ka video mila. Lamba video / aur clips add karo.")
        left, trimmed = target, []                        # hard-trim to the exact target
        for s_, e_ in sorted(ivs):
            if left <= 0.02:
                break
            e2 = min(e_, s_ + left)
            trimmed.append((s_, e2))
            left -= e2 - s_
        ivs = [x for x in trimmed if x[1] - x[0] >= 0.1] or trimmed[:1]            # no tiny slivers
        miss = min(target, have) - _total(ivs)
        if miss > 0.02 and ivs:                           # a dropped sliver must not make the video shorter
            ivs = _fill_gaps(ivs, window, _total(ivs) + miss, used_fumbles)
        if ivs and an.transcript.segments:                # land the last cut on the end of a word - only if that keeps the length (+-0.02s)
            ends = [w.end for sg in an.transcript.segments for w in sg.words] or [sg.end for sg in an.transcript.segments]
            last_s, last_e = ivs[-1]
            near = [x for x in ends if abs(x - last_e) <= 0.02 and x - last_s >= MIN_CLIP and x <= dur]
            if near:
                ivs[-1] = (last_s, min(max(near, key=lambda x: -abs(x - last_e)), dur))
    if not ivs:
        ivs = [window]

    scores = {}
    for c in chosen:
        for iv in c[2]:
            scores[iv] = c[1]
    clips = []
    for n, (s, e) in enumerate(ivs, 1):
        best = max((sc for (a, b), sc in scores.items() if a < e and b > s), default=0.0)
        lbl = next((c[3] for c in chosen if any(a < e and b > s for a, b in c[2])), "")
        clips.append(Clip(id=f"c{n}", source_start=round(s, 3), source_end=round(e, 3),
                          label=lbl[:80], score=best))

    if req.zoom or req.sfx:
        clips = split_at_phrases(clips, an, req)

    ui_cap = _ui_caption_style(source_path)
    w, h = resolution_for(req.aspect_ratio, meta.width, meta.height, req)
    edl = EDL(
        source_media=SourceMedia(path=source_path, duration=dur, width=meta.width, height=meta.height,
                                 fps=meta.fps, has_audio=meta.has_audio),
        project=ProjectSettings(duration=0, aspect_ratio=req.aspect_ratio, width=w, height=h,
                                fps=min(max(meta.fps, 24), 60), fit=("blur" if req.reframe == "blur" else req.fit),
                                color_grade=req.color_grade, look=look),
        clips=clips,
        audio=AudioSettings(music=req.music_required, ducking=meta.has_audio,
                            denoise=req.denoise and meta.has_audio, denoise_level=req.denoise_level,
                            music_style=req.music_style, music_path=_user_music(source_path)),
        caption_settings=CaptionSettings(
            enabled=req.caption_required, style="styled",
            preset=ui_cap.get("preset") or (req.caption_style if req.caption_style != "auto" else "classic"),
            size=ui_cap.get("size") or req.caption_size, margin_pct=req.caption_margin),
        requirements=req.model_dump(),
    )
    edl.project.duration = edl.timeline_duration()
    if req.zoom:
        edl.effects = [Effect(clip_id=c.id, zoom=ZOOM_LEVEL[req.zoom_strength]) for i, c in enumerate(clips) if i % 2 == 1]
    if req.transitions:
        for prev, c in zip(clips, clips[1:]):
            if c.source_start - prev.source_end > 1.0:    # fade only across real jumps
                edl.transitions.append(Transition(type="fade", clip_id=c.id, duration=0.25))
    if req.title:
        edl.overlays = [Overlay(text=req.title, start=0.0, end=round(min(2.5, edl.project.duration), 2),
                                position="center")]
    if req.caption_required:
        if req.caption_script:                                # captions dictated in the prompt win
            edl.captions = script_to_captions(req.caption_script, edl.project.duration, edl.caption_settings)
        else:
            edl.captions = build_captions(edl.clips, an.transcript, edl.caption_settings)
    if req.sfx:
        edl.audio.sfx = True
        edl.audio.sfx_events = build_sfx(edl)
    edl.reframes, reframe_notes = _reframes(edl, req, source_path)

    # explainability
    kept = edl.project.duration
    ex = []
    if not look.is_default:
        ex.append(f"Look & effects applied: {look.describe()}")
    if req.preserve_timeline:
        ex.append("Kept every frame of the video (no cuts) — only enhancements were applied")
    elif dur - kept > 0.05:
        ex.append(f"Removed {max(dur - kept, 0):.1f}s in total from the original {dur:.1f}s")
    ex += _brain_notes(used_fumbles)
    ex.append(f"Selected {len(clips)} clip(s); final length {kept:.1f}s"
              + (f" (target {req.target_duration:.0f}s)" if req.target_duration else ""))
    if transcript_used:
        ex.append(f"Used {len(chosen)} of {len(cand)} transcript segments")
    else:
        ex.append("No transcript available — planned from speech/silence regions only")
    if req.aspect_ratio != "original":
        ex.append(f"Converted to {req.aspect_ratio} ({w}x{h}, {edl.project.fit})")
    ex += reframe_notes
    ex += [f"Style: {n}" for n in req.notes]
    if req.caption_required and edl.caption_settings.preset != "classic":
        ex.append(f"Caption look: {edl.caption_settings.preset}, {edl.caption_settings.size} size")
    if req.caption_required:
        src_txt = "from the caption script in your prompt" if req.caption_script else "from the transcript"
        ex.append(f"Added {len(edl.captions)} caption block(s) {src_txt}")
    if edl.audio.sfx_events:
        ex.append(f"Added {len(edl.audio.sfx_events)} sound effect(s) (whoosh on cuts, pop/click/ding on captions)")
    if req.zoom and len(clips) > 1:
        ex.append(f"Punch-in zoom on {len(edl.effects)} phrase cut(s)")
    if req.music_required:
        src_m = "your uploaded track" if edl.audio.music_path else f"a built-in {req.music_style} track"
        ex.append(f"Added background music ({src_m}), lowered automatically while someone is speaking")
    if edl.audio.denoise:
        ex.append(f"Removed background noise from the audio ({edl.audio.denoise_level} spectral noise reduction) — video untouched")
    if edl.project.color_grade:
        ex.append("Applied natural cinematic color correction for balanced lighting and warm skin tones")
    if edl.overlays:
        ex.append(f"Added title card: \"{edl.overlays[0].text}\"")
    if length_warning:
        ex.append("WARNING: " + length_warning)
        edl.warnings.append(length_warning)
    edl.explanation = ex
    return edl


def split_at_phrases(clips: List[Clip], an: AnalysisBundle, req: Requirements,
                     min_len: float = 2.0, max_len: float = 4.5) -> List[Clip]:
    """Split long contiguous clips at phrase boundaries so punch-in zooms / whoosh SFX have cuts to land on.
    Boundaries come from the dictated caption script, else transcript segment ends and word gaps.
    The split is contiguous (no frames dropped) so audio stays in sync."""
    if getattr(req, "shot_len", None):                      # reference pace: clip length follows the reference's shot length
        min_len, max_len = max(1.0, req.shot_len * 0.7), max(req.shot_len * 1.4, 1.8)
    cut_pts: List[float] = []
    if req.caption_script:
        off = 0.0                                             # script is on the OUTPUT timeline -> source time
        for c in clips:
            for ln in req.caption_script:
                if 0 < ln.start - off < c.duration:
                    cut_pts.append(c.source_start + (ln.start - off))
            off += c.duration
    else:
        for sg in an.transcript.segments:
            cut_pts.append(sg.end)
            for a, b in zip(sg.words, sg.words[1:]):
                if b.start - a.end >= 0.2:
                    cut_pts.append((a.end + b.start) / 2)
    out: List[Clip] = []
    for c in clips:
        pts = sorted(p for p in cut_pts if c.source_start + 0.8 < p < c.source_end - 0.8)
        chosen_pts, last = [], c.source_start
        for p in pts:
            if p - last >= min_len:
                chosen_pts.append(p)
                last = p
        bounds = [c.source_start] + chosen_pts + [c.source_end]
        final = [bounds[0]]                                   # enforce max_len even with no phrase gaps
        for b in bounds[1:]:
            while b - final[-1] > max_len * 1.6:
                final.append(round(final[-1] + max_len, 3))
            final.append(b)
        for a, b in zip(final, final[1:]):
            out.append(Clip(id="", source_start=round(a, 3), source_end=round(b, 3), label=c.label, score=c.score))
    for i, c in enumerate(out, 1):
        c.id = f"c{i}"
    return out


def build_sfx(edl: EDL, limit: int = 40) -> List[SfxEvent]:
    """whoosh on every cut, pop/click/ding cycling on caption appearances."""
    ev: List[SfxEvent] = []
    t = 0.0
    for i, c in enumerate(edl.clips):
        if i > 0:
            ev.append(SfxEvent(type="whoosh", time=round(max(t - 0.12, 0), 3)))
        t += c.duration
    cycle = ["pop", "click", "ding"]
    last = -1.0
    for i, cp in enumerate(edl.captions):
        if cp.start - last >= 0.5:
            ev.append(SfxEvent(type=cycle[i % 3], time=round(cp.start, 3)))
            last = cp.start
    ev = sorted((e for e in ev if e.time < edl.project.duration - 0.05), key=lambda e: e.time)
    return ev[:limit]


def edl_preview(edl: EDL) -> dict:
    """Human-readable view for the frontend (no raw JSON)."""
    def tc(t: float) -> str:
        return f"{int(t // 60):02d}:{t % 60:05.2f}"
    t = 0.0
    rows = []
    for c in edl.clips:
        rows.append({"id": c.id, "source": f"{tc(c.source_start)} → {tc(c.source_end)}",
                     "timeline_start": tc(t), "length": round(c.duration, 2), "text": c.label,
                     "zoom": any(e.clip_id == c.id for e in edl.effects),
                     "fade_in": any(x.clip_id == c.id and x.type == "fade" for x in edl.transitions)})
        t += c.duration
    return {"duration": round(edl.timeline_duration(), 2), "aspect_ratio": edl.project.aspect_ratio,
            "resolution": f"{edl.project.width}x{edl.project.height}", "captions": edl.caption_settings.enabled,
            "music": edl.audio.music, "denoise": edl.audio.denoise,
            "color_grade": getattr(edl.project, "color_grade", False),
            "look": edl.project.look.describe(),
            "caption_style": edl.caption_settings.preset, "fit": edl.project.fit,
            "reframe": [{"clip": r.clip_id, "face_ratio": r.face_ratio, "axis": r.axis} for r in edl.reframes],
            "overlays": [o.text for o in edl.overlays], "clips": rows, "explanation": edl.explanation}