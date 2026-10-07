"""ACT stage: EDL -> FFmpeg commands (built by us, never by an LLM) -> draft.mp4.

Pass 1: trim + aspect fit + punch-in zoom + fades + concat  -> work/base.mp4
Pass 2: caption burn-in + background music with ducking     -> output/draft.mp4
Every advanced feature has a fallback: failure never kills the render, it degrades.
"""
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from .. import config
from ..models.edl import EDL
from ..utils.ffmpeg import FFmpegError, run
from . import captions as cap
from . import denoise as dn_mod
from . import music as music_mod


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    path: Path
    warnings: List[str] = field(default_factory=list)


def _fit(w: int, h: int, mode: str, aspect: str) -> str:
    if aspect == "original":
        return f",scale={w}:{h}"
    if mode == "pad":
        return (f",scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")
    return f",scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def _auto_gain(path: Path) -> float:
    """gain (dB) that brings the recording's integrated loudness to -16 LUFS, limited to -10..+12 dB.
    The cap matters: a noise-only / very quiet track must not be pumped up."""
    try:
        err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"]).stderr
        vals = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", err)
        lufs = float(vals[-1])
        if lufs < -60:                                   # (almost) silent: leave it alone
            return 0.0
        return round(max(-10.0, min(12.0, -16.0 - lufs)), 2)
    except Exception:
        return 5.0


def build_base_cmd(edl: EDL, out: Path, use_effects: bool, denoise: bool = False,
                   clean_audio: Optional[Path] = None, normalize: bool = True, gain_db: float = 5.0) -> List[str]:
    """clean_audio: pre-denoised wav (same timeline as the source) used instead of the source's own audio.
    normalize / gain_db: lift a quiet recording towards -16 LUFS with a measured, capped gain (see _auto_gain),
    so the result is never too quiet but residual noise is never boosted wildly."""
    p, n = edl.project, len(edl.clips)
    W, H = p.width, p.height
    zoom = {e.clip_id: e.zoom for e in edl.effects} if use_effects else {}
    fades_in = {t.clip_id: t.duration for t in edl.transitions if t.type == "fade"} if use_effects else {}
    has_a = edl.source_media.has_audio and edl.audio.keep_original
    parts, labels = [], []
    for i, c in enumerate(edl.clips):
        d = c.duration
        v = f"[0:v]trim=start={c.source_start:.3f}:end={c.source_end:.3f},setpts=PTS-STARTPTS"
        v += _fit(W, H, p.fit, p.aspect_ratio)
        if c.id in zoom:
            z = max(1.0, min(zoom[c.id], 2.0))
            v += f",crop=iw/{z}:ih/{z},scale={W}:{H}"
        if getattr(p, "color_grade", False) and use_effects:
            v += ",eq=contrast=1.06:brightness=0.01:saturation=1.10:gamma=1.02,colorbalance=rs=0.05:gs=0.01:bs=-0.03:rm=0.04:gm=0.02:bm=-0.03,unsharp=5:5:0.6:5:5:0.0"
        fin = min(fades_in.get(c.id, 0), d / 3) if i > 0 else 0
        nxt = edl.clips[i + 1].id if i + 1 < n else None
        fout = min(fades_in.get(nxt, 0), d / 3) if nxt else 0
        if fin:
            v += f",fade=t=in:st=0:d={fin:.3f}"
        if fout:
            v += f",fade=t=out:st={d - fout:.3f}:d={fout:.3f}"
        v += f",fps={p.fps:g},format=yuv420p,setsar=1[v{i}]"
        parts.append(v)
        labels.append(f"[v{i}]")
        if has_a:
            a_src = "[1:a]" if clean_audio else "[0:a]"      # (clean_audio is only passed when the source has audio)
            a = (f"{a_src}atrim=start={c.source_start:.3f}:end={c.source_end:.3f},asetpts=PTS-STARTPTS,"
                 f"aresample=44100,aformat=channel_layouts=stereo")
            if denoise:                                         # fallback filter chain (used only if spectral denoise failed)
                a += ",highpass=f=80,afftdn=nr=25:nf=-35:tn=1,afftdn=nr=15:nf=-35:tn=1,lowpass=f=10000"
            if not normalize:
                a += ",volume=1.8,alimiter=limit=0.95"
            if fin:
                a += f",afade=t=in:st=0:d={fin:.3f}"
            if fout:
                a += f",afade=t=out:st={d - fout:.3f}:d={fout:.3f}"
            parts.append(a + f"[a{i}]")
            labels.append(f"[a{i}]")
    if has_a and normalize:
        parts.append("".join(labels) + f"concat=n={n}:v=1:a=1[v][acat]")
        parts.append(f"[acat]volume={gain_db:.2f}dB,alimiter=limit=0.89,aresample=44100,aformat=channel_layouts=stereo[a]")
    else:
        parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_a else 0}[v]" + ("[a]" if has_a else ""))
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", edl.source_media.path] + (["-i", str(clean_audio)] if clean_audio and has_a else [])
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if has_a:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", str(out)]
    return cmd


def _resolve_music(edl: EDL, work: Path, seconds: float) -> Path:
    """priority: track uploaded for this job > file in assets/music > built-in synthesized track"""
    if edl.audio.music_path and Path(edl.audio.music_path).exists():
        return Path(edl.audio.music_path)
    if config.ASSETS_MUSIC_DIR.exists():
        for f in sorted(config.ASSETS_MUSIC_DIR.iterdir()):
            if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"):
                return f
    return music_mod.make_track(work / f"music_{edl.audio.music_style}.wav", edl.audio.music_style)


_SFX_SRC = {   # synthesized offline with lavfi: no copyright, no downloads
    "whoosh": "anoisesrc=d=0.45:c=pink:a=0.7:r=44100,highpass=f=250,lowpass=f=5000,"
              "afade=t=in:d=0.22,afade=t=out:st=0.22:d=0.23",
    "pop":    "aevalsrc='0.9*sin(2*PI*(450+2800*t)*t)*exp(-38*t)':d=0.15:s=44100",
    "click":  "anoisesrc=d=0.03:c=white:a=0.9:r=44100,highpass=f=1800,afade=t=out:d=0.03",
    "ding":   "aevalsrc='0.55*sin(2*PI*1320*t)*exp(-5*t)+0.3*sin(2*PI*1980*t)*exp(-7*t)':d=0.9:s=44100",
}


def _sfx_file(kind: str, work: Path) -> Path:
    out = work / f"sfx_{kind}.wav"
    if not out.exists():
        run(["ffmpeg", "-y", "-hide_banner", "-f", "lavfi", "-i", _SFX_SRC[kind], "-ac", "2", str(out)])
    return out


def _apply_sfx(video: Path, edl: EDL, work: Path) -> Path:
    """Mix SFX on top of the finished draft (video stream is copied, speech stays untouched)."""
    events = edl.audio.sfx_events
    kinds = sorted({e.type for e in events})
    files = {k: _sfx_file(k, work) for k in kinds}
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(video)]
    for k in kinds:
        cmd += ["-i", str(files[k])]
    idx = {k: i + 1 for i, k in enumerate(kinds)}
    fc, labels = [], []
    for n, e in enumerate(events):
        ms = int(max(e.time, 0) * 1000)
        fc.append(f"[{idx[e.type]}:a]adelay={ms}|{ms},volume={edl.audio.sfx_volume}[s{n}]")
        labels.append(f"[s{n}]")
    fc.append(f"[0:a]{''.join(labels)}amix=inputs={len(events) + 1}:duration=first:normalize=0,"
              f"alimiter=limit=0.95[aout]")
    out = work / "with_sfx.mp4"
    cmd += ["-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    run(cmd)
    return out


def _post_cmd(base: Path, out: Path, edl: EDL, work: Path, cap_mode: Optional[str],
              music: Optional[Path], has_audio: bool, overlays: bool = False) -> List[str]:
    p = edl.project
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(base)]
    if music:
        cmd += ["-i", str(music)]
    fc, vf, vmap, amap = [], [], "0:v", "0:a?"
    if cap_mode == "styled":
        cap.write_ass(edl.captions, work / "captions.ass", p.width, p.height)
        vf.append("ass=captions.ass")
    elif cap_mode == "plain":
        cap.write_srt(edl.captions, work / "captions.srt")
        fs = max(18, int(min(p.width, p.height) * 0.06))
        vf.append(f"subtitles=captions.srt:original_size={p.width}x{p.height}:force_style="
                  f"'FontSize={fs},Alignment=2,Outline=2,MarginV={int(p.height * 0.1)}'")
    if overlays:
        cap.write_overlay_ass(edl.overlays, work / "overlays.ass", p.width, p.height)
        vf.append("ass=overlays.ass")
    if vf:
        fc.append("[0:v]" + ",".join(vf) + "[vout]")
        vmap = "[vout]"
    if music:
        mv, total = edl.audio.music_volume, edl.timeline_duration()
        # loop -> cut to video length -> match loudness (-20 LUFS, i.e. a bit softer than the -16 LUFS speech) -> fade in/out
        chain = (f"[1:a]aloop=loop=-1:size=2147483647,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aresample=44100,"
                 f"aformat=channel_layouts=stereo,loudnorm=I=-20:TP=-2:LRA=7,aresample=44100,volume={mv / 0.5:.3f},"
                 f"afade=t=in:d=0.8,afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5")
        if has_audio and edl.audio.ducking:
            # music drops ~8-10 dB while somebody speaks and comes back up in the pauses
            fc.append(chain + "[m];[0:a]asplit=2[sc][mix];"
                      "[m][sc]sidechaincompress=threshold=0.04:ratio=4:attack=20:release=450[duck];"
                      "[mix][duck]amix=inputs=2:duration=first:normalize=0[aout]")
        elif has_audio:
            fc.append(chain + "[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[aout]")
        else:
            fc.append(chain + "[aout]")
        amap = "[aout]"
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]
    cmd += ["-map", vmap, "-map", amap]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"] if vf else ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "160k"] if music else ["-c:a", "copy"]
    return cmd + ["-shortest", "-movflags", "+faststart", str(out)]


def render(edl: EDL, job_dir: Path, level: int = 0) -> RenderResult:
    """level: 0 = everything, 1 = no effects/transitions, 2 = plain captions & no music, 3 = bare cuts."""
    work, out_dir = (job_dir / "work").resolve(), (job_dir / "output").resolve()
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    if not edl.clips:
        raise RenderError("EDL has no clips")
    base, draft = work / "base.mp4", out_dir / "draft.mp4"
    has_effects = bool(edl.effects or edl.transitions or getattr(edl.project, "color_grade", False))
    want_dn = level < 3 and edl.audio.denoise and edl.source_media.has_audio
    clean_wav: Optional[Path] = None
    if want_dn:                                           # audio-only noise removal; video + timeline stay untouched
        try:
            clean_wav = dn_mod.clean_audio(Path(edl.source_media.path), work / "audio_clean.wav", work,
                                           edl.audio.denoise_level, edl.source_media.duration)
        except Exception:
            clean_wav = None                              # -> falls back to the ffmpeg filter chain below
    # (use effects, noise mode, loudness-normalize) from richest to safest
    gain_db = _auto_gain(clean_wav or Path(edl.source_media.path)) if edl.source_media.has_audio else 0.0
    first_dn = "spectral" if clean_wav else ("chain" if want_dn else "off")
    attempts = [(level < 1, first_dn, True)]
    if level < 1 and has_effects:
        attempts.append((False, first_dn, True))
    if want_dn and first_dn == "spectral":
        attempts.append((False, "chain", True))
    if want_dn:
        attempts.append((False, "off", True))
    attempts.append((False, "off", False))
    seen, uniq = set(), []
    for at in attempts:
        if at not in seen:
            seen.add(at)
            uniq.append(at)
    attempts = uniq
    last: Optional[Exception] = None
    for i, (fx, dnm, norm) in enumerate(attempts):
        try:
            run(build_base_cmd(edl, base, use_effects=fx, denoise=(dnm == "chain"),
                               clean_audio=clean_wav if dnm == "spectral" else None, normalize=norm, gain_db=gain_db))
            if i > 0:
                if fx != attempts[0][0]:
                    warnings.append("Effects/transitions failed — fell back to hard cuts")
                if want_dn and dnm == "off":
                    warnings.append("Noise reduction failed — exported without it")
                elif want_dn and dnm != first_dn:
                    warnings.append("Spectral noise reduction failed — used the simpler filter")
                if not norm:
                    warnings.append("Loudness normalisation failed — exported without it")
            elif want_dn and first_dn == "chain":
                warnings.append("Spectral noise reduction unavailable (numpy?) — used the simpler filter")
            last = None
            break
        except FFmpegError as e:
            last = e
    if last is not None:
        raise RenderError(f"Base render failed: {last}")

    want_caps = level < 3 and edl.caption_settings.enabled and bool(edl.captions)
    want_ov = level < 3 and bool(edl.overlays)
    want_music = level < 2 and edl.audio.music
    cap_modes: List[Optional[str]] = []
    if want_caps:
        cap_modes = ["styled", "plain"] if (level < 2 and edl.caption_settings.style == "styled") else ["plain"]
    music_path: Optional[Path] = None
    if want_music:
        try:
            music_path = _resolve_music(edl, work, edl.timeline_duration())
        except FFmpegError:
            warnings.append("Could not prepare music — rendering without it")
    combos = []           # (caption_mode, music, overlays) from richest to plainest
    for ov in ([True, False] if want_ov else [False]):
        for mu in ([music_path, None] if music_path else [None]):
            for cm in cap_modes + [None]:
                combos.append((cm, mu, ov))
    seen, ordered = set(), []
    for c in combos:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    for i, (cm, mu, ov) in enumerate(ordered):
        if cm is None and mu is None and not ov:
            shutil.copy(base, draft)
            if i > 0 or want_caps or want_music or want_ov:
                warnings.append("Captions/music/title could not be added — exported plain cut")
            break
        try:
            run(_post_cmd(base, draft, edl, work, cm, mu, edl.source_media.has_audio, ov), cwd=work)
            if i > 0:
                warnings.append(f"Fell back to captions={cm or 'off'}, music={'on' if mu else 'off'}, "
                                f"title={'on' if ov else 'off'}")
            break
        except FFmpegError:
            continue
    if edl.audio.sfx and edl.audio.sfx_events and level < 2 and edl.source_media.has_audio:
        try:
            shutil.copy(_apply_sfx(draft, edl, work), draft)
        except Exception:
            warnings.append("Sound effects could not be mixed — exported without them")
    if edl.captions:
        try:
            cap.write_vtt(edl.captions, out_dir / "final.vtt")
            cap.write_srt(edl.captions, out_dir / "final.srt")
            cap.write_vtt(edl.captions, work / "captions.vtt")
        except Exception:
            pass
    return RenderResult(path=draft, warnings=warnings)