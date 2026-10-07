"""Captions: build timeline captions from the transcript + EDL clips, write SRT / ASS."""
from pathlib import Path
from typing import List
from ..models.edl import Caption, CaptionWord, Clip
from ..models.transcript import Transcript

MAX_WORDS = 7
MAX_SECONDS = 2.8


def _chunk(words: List[CaptionWord]) -> List[List[CaptionWord]]:
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= MAX_WORDS or (w.end - cur[0].start) >= MAX_SECONDS or w.text.endswith((".", "?", "!")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_captions(clips: List[Clip], tr: Transcript) -> List[Caption]:
    """Map transcript onto the output timeline (after cuts)."""
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
        for ch in _chunk(words):
            caps.append(Caption(start=ch[0].start, end=ch[-1].end,
                                text=" ".join(w.text for w in ch), words=ch))
        offset += c.duration
    # avoid overlaps between consecutive captions
    for a, b in zip(caps, caps[1:]):
        if a.end > b.start:
            a.end = max(a.start + 0.1, b.start - 0.01)
    return caps


def script_to_captions(script, total: float) -> List[Caption]:
    """Captions the user dictated in the prompt (timed on the OUTPUT timeline).
    Long lines are split into <=MAX_WORDS chunks with word timing spread over the line."""
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
        for ch in _chunk(words):
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


def write_ass(caps: List[Caption], path: Path, width: int, height: int) -> Path:
    """Styled captions: bold, outlined, with word-level karaoke highlight when word times exist."""
    size = max(24, int(min(width, height) * 0.075))
    margin_v = int(height * 0.12)
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,Arial,{size},&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,{margin_v},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    rows = []
    for c in caps:
        if c.words:
            parts = []
            for w in c.words:
                cs = max(1, int(round((w.end - w.start) * 100)))
                parts.append(f"{{\\kf{cs}}}{_clean(w.text)}")
            text = " ".join(parts)
        else:
            text = _clean(c.text)
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
