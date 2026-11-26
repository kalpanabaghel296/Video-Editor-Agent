"""Captions: build timeline captions from the transcript + EDL clips, write SRT / ASS."""
from pathlib import Path
from typing import List, Optional, Tuple
from ..models.edl import Caption, CaptionWord, Clip
from ..models.transcript import Transcript

MAX_WORDS = 7
MAX_SECONDS = 2.8
# words per caption block / max seconds per block, per caption preset
CHUNKING = {"classic": (7, 2.8), "hormozi": (3, 1.6), "minimal": (6, 3.0), "boxed": (6, 2.8), "karaoke": (5, 2.2)}


def chunk_params(preset: str = "classic", max_words: Optional[int] = None) -> Tuple[int, float]:
    mw, ms = CHUNKING.get(preset, CHUNKING["classic"])
    return (max_words or mw), ms


def _chunk(words: List[CaptionWord], max_words: int = MAX_WORDS, max_seconds: float = MAX_SECONDS) -> List[List[CaptionWord]]:
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or (w.end - cur[0].start) >= max_seconds or w.text.endswith((".", "?", "!")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_captions(clips: List[Clip], tr: Transcript, settings=None) -> List[Caption]:
    """Map transcript onto the output timeline (after cuts). `settings` (CaptionSettings) picks the block size."""
    mw, ms = chunk_params(getattr(settings, "preset", "classic"), getattr(settings, "max_words", None))
    caps: List[Caption] = []
    offset = 0.0
    for c in clips:
        if c.duration <= 0:
            continue
        words: List[CaptionWord] = []
        for seg in tr.segments:
            if seg.end <= c.source_start or seg.start >= c.source_end:
                continue
            if seg.words:
                src = [(w.text, w.start, w.end) for w in seg.words]
            else:                                  # estimate per-word timing
                toks = seg.text.split()
                total = sum(len(t) for t in toks) or 1
                t, src = seg.start, []
                for tok in toks:
                    d = (seg.end - seg.start) * len(tok) / total
                    src.append((tok, t, t + d))
                    t += d
            for text, s, e in src:
                mid = (s + e) / 2
                if c.source_start <= mid <= c.source_end and text:
                    ns = offset + max(s, c.source_start) - c.source_start
                    ne = offset + min(e, c.source_end) - c.source_start
                    words.append(CaptionWord(text=text, start=round(ns, 3), end=round(max(ne, ns + 0.05), 3)))
        for ch in _chunk(words, mw, ms):
            caps.append(Caption(start=ch[0].start, end=ch[-1].end,
                                text=" ".join(w.text for w in ch), words=ch))
        offset += c.duration
    # avoid overlaps between consecutive captions
    for a, b in zip(caps, caps[1:]):
        if a.end > b.start:
            a.end = max(a.start + 0.1, b.start - 0.01)
    return caps


def script_to_captions(script, total: float, settings=None) -> List[Caption]:
    """Captions the user dictated in the prompt (timed on the OUTPUT timeline).
    Long lines are split into <=MAX_WORDS chunks with word timing spread over the line."""
    mw, ms = chunk_params(getattr(settings, "preset", "classic"), getattr(settings, "max_words", None))
    caps: List[Caption] = []
    for ln in script:
        s0, e0 = max(ln.start, 0.0), min(ln.end, total) if total else ln.end
        toks = ln.text.split()
        if not toks or e0 <= s0:
            continue
        weight = sum(len(t) + 1 for t in toks)
        t, words = s0, []
        for tok in toks:
            d = (e0 - s0) * (len(tok) + 1) / weight
            words.append(CaptionWord(text=tok, start=round(t, 3), end=round(t + d, 3)))
            t += d
        for ch in _chunk(words, mw, ms):
            caps.append(Caption(start=ch[0].start, end=ch[-1].end, text=" ".join(w.text for w in ch), words=ch))
    for a, b in zip(caps, caps[1:]):
        if a.end > b.start:
            a.end = max(a.start + 0.1, b.start - 0.01)
    return caps


def transcript_to_captions(tr: Transcript) -> List[Caption]:
    """Build captions for the entire source/uploaded video from transcript."""
    words: List[CaptionWord] = []
    for seg in tr.segments:
        if seg.words:
            src = [(w.text.strip(), w.start, w.end) for w in seg.words if w.text.strip()]
        else:
            toks = seg.text.strip().split()
            total = sum(len(t) for t in toks) or 1
            t, src = seg.start, []
            for tok in toks:
                d = (seg.end - seg.start) * len(tok) / total
                src.append((tok, t, t + d))
                t += d
        for text, s, e in src:
            if text:
                words.append(CaptionWord(text=text, start=round(s, 3), end=round(max(e, s + 0.05), 3)))

    caps: List[Caption] = []
    for ch in _chunk(words):
        caps.append(Caption(start=ch[0].start, end=ch[-1].end,
                            text=" ".join(w.text for w in ch), words=ch))
    for a, b in zip(caps, caps[1:]):
        if a.end > b.start:
            a.end = max(a.start + 0.1, b.start - 0.01)
    return caps


def _srt_time(t: float) -> str:
    t = max(t, 0)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}".replace(",1000", ",999")


def _vtt_time(t: float) -> str:
    t = max(t, 0)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d}.{int(round((s % 1) * 1000)):03d}".replace(".1000", ".999")


def write_srt(caps: List[Caption], path: Path) -> Path:
    lines = []
    for i, c in enumerate(caps, 1):
        lines += [str(i), f"{_srt_time(c.start)} --> {_srt_time(c.end)}", c.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_vtt(caps: List[Caption], path: Path) -> Path:
    lines = ["WEBVTT", ""]
    for i, c in enumerate(caps, 1):
        lines += [str(i), f"{_vtt_time(c.start)} --> {_vtt_time(c.end)}", c.text, ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ass_time(t: float) -> str:
    t = max(t, 0)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\n", " ")


_ASS_FORMAT = ("Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
               "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
               "MarginV,Encoding\n")
SIZE_K = {"small": 0.8, "normal": 1.0, "large": 1.25}
# colours are &HAABBGGRR
_STYLES = {
    # classic = the original look: yellow sweep over white, Arial bold
    "classic": dict(size=0.075, bold=-1, primary="&H0000E5FF", secondary="&H00FFFFFF", outline="&H00000000",
                    back="&H64000000", border=1, outline_w=3, shadow=1, mode="sweep", upper=False, font="Arial"),
    # hormozi = big bold ALL CAPS, 2-3 words at a time, the spoken word turns yellow
    "hormozi": dict(size=0.098, bold=-1, primary="&H00FFFFFF", secondary="&H00FFFFFF", outline="&H00000000",
                    back="&H00000000", border=1, outline_w=7, shadow=0, mode="active", upper=True, font="Arial Black",
                    highlight="&H0000E5FF"),
    "minimal": dict(size=0.052, bold=0, primary="&H00FFFFFF", secondary="&H00FFFFFF", outline="&H00000000",
                    back="&H80000000", border=1, outline_w=2, shadow=1, mode="plain", upper=False, font="Arial"),
    # boxed = white text on a semi-transparent dark box
    "boxed": dict(size=0.062, bold=-1, primary="&H00FFFFFF", secondary="&H00FFFFFF", outline="&H96000000",
                  back="&H96000000", border=3, outline_w=9, shadow=0, mode="plain", upper=False, font="Arial"),
    # karaoke = word-by-word green sweep
    "karaoke": dict(size=0.08, bold=-1, primary="&H0060FF40", secondary="&H00FFFFFF", outline="&H00000000",
                    back="&H64000000", border=1, outline_w=4, shadow=1, mode="sweep", upper=False, font="Arial"),
}


def has_devanagari(text: str) -> bool:
    return any("\u0900" <= ch <= "\u097f" for ch in text)


def pick_font(caps: List[Caption], style: dict, override: Optional[str] = None, language: Optional[str] = None) -> str:
    """Arial has no Indic glyphs -> use a font of the script that is actually in the captions
    (Windows: Nirmala UI for Hindi, Kannada, Tamil, Telugu, Bengali ...). Latin text keeps the preset's font."""
    if override:
        return override
    from .languages import detect_script, font_for_script, script_of
    script = detect_script(" ".join(c.text for c in caps)) or (script_of(language) if language else None)
    return font_for_script(script) or style["font"]


def write_ass(caps: List[Caption], path: Path, width: int, height: int, settings=None) -> Path:
    """Styled captions. `settings` (CaptionSettings) chooses preset / size / safe-zone margin / font."""
    preset = getattr(settings, "preset", "classic") or "classic"
    st = _STYLES.get(preset, _STYLES["classic"])
    size = max(22, int(min(width, height) * st["size"] * SIZE_K.get(getattr(settings, "size", "normal"), 1.0)))
    default_margin = 0.18 if height > width else 0.12            # vertical video: stay clear of the app's UI
    margin_v = int(height * (getattr(settings, "margin_pct", None) or default_margin))
    font = pick_font(caps, st, getattr(settings, "font", None), getattr(settings, "language", None))
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n" + _ASS_FORMAT +
        f"Style: Default,{font},{size},{st['primary']},{st['secondary']},{st['outline']},{st['back']},{st['bold']},0,0,0,"
        f"100,100,0,0,{st['border']},{st['outline_w']},{st['shadow']},2,40,40,{margin_v},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    def fix(s: str) -> str:
        s = _clean(s)
        return s.upper() if st["upper"] else s

    rows = []
    for c in caps:
        ws = c.words
        if st["mode"] == "active" and ws:
            for i, w in enumerate(ws):                            # one event per spoken word, that word highlighted
                end = ws[i + 1].start if i + 1 < len(ws) else max(c.end, w.end)
                end = max(end, w.start + 0.06)
                txt = " ".join(("{\\c&H" + st["highlight"][-6:] + "&}" + fix(x.text) + "{\\c&H" + st["primary"][-6:] + "&}") if j == i
                               else fix(x.text) for j, x in enumerate(ws))
                rows.append(f"Dialogue: 0,{_ass_time(w.start)},{_ass_time(end)},Default,,0,0,0,,{txt}")
            continue
        if st["mode"] == "sweep" and ws:
            text = " ".join(f"{{\\kf{max(1, int(round((w.end - w.start) * 100)))}}}{fix(w.text)}" for w in ws)
        else:
            text = fix(c.text)
        rows.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{text}")
    path.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def write_overlay_ass(overlays, path: Path, width: int, height: int) -> Path:
    """Title cards / on-screen text with fade in+out. Alignment: 8=top, 5=center, 2=bottom."""
    size = max(28, int(min(width, height) * 0.09))
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Title,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,2,0,3,6,0,5,60,60,{int(height * 0.1)},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    align = {"top": 8, "center": 5, "bottom": 2}
    rows = [f"Dialogue: 1,{_ass_time(o.start)},{_ass_time(o.end)},Title,,0,0,0,,"
            f"{{\\an{align.get(o.position, 5)}\\fad(350,350)}}{_clean(o.text)}" for o in overlays]
    path.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    return path