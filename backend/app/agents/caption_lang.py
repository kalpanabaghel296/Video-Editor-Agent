"""Caption language: captions in Hindi / Kannada / Tamil / Telugu / Bengali / Spanish / English.

  * same language as the speech  -> the words are written in that language's OWN script. The normal transcript is
    Roman-script Hinglish on purpose, so for Hindi / Kannada ... Whisper runs once more in that language (cached).
  * different language           -> the transcript sentences are translated (cached), then spread over the time of the
    original words so the highlight styles (karaoke / hormozi) still follow the speech.
Anything that fails (no internet, no Whisper) leaves the normal captions in place and returns a warning for the user.
"""
import hashlib
import json
from pathlib import Path
from typing import List, Optional

from ..analysis import transcription, translate
from ..editing.captions import build_captions
from ..editing.languages import BY_CODE, has_script, normalize, script_of
from ..models.edl import EDL
from ..models.transcript import Segment, Transcript, Word


def _text(tr: Transcript) -> str:
    return " ".join(s.text for s in tr.segments)


def _hash(tr: Transcript) -> str:
    return hashlib.sha1(_text(tr).encode("utf-8")).hexdigest()[:16]


def _load(f: Path, key: Optional[str]) -> Optional[Transcript]:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if key is None or d.get("key") == key:
            return Transcript(**d["transcript"])
    except Exception:
        pass
    return None


def _save(f: Path, key: Optional[str], tr: Transcript) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"key": key, "transcript": tr.model_dump()}, ensure_ascii=False), encoding="utf-8")


def _time_at(words: List[Word], pos: float, end: bool) -> float:
    """time of a fractional word position (0..N) on the original word timeline"""
    n = len(words)
    if pos >= n:
        return words[-1].end
    i = int(pos)
    w = words[i]
    return w.start + (pos - i) * (w.end - w.start)


def retime(seg: Segment, new_text: str) -> Segment:
    """New text for a segment: its words are laid over the ORIGINAL speech timing (token j of M -> position j*N/M)."""
    toks = new_text.split()
    if not toks:
        return Segment(start=seg.start, end=seg.end, text=new_text, words=[])
    src = seg.words or []
    m, words = len(toks), []
    if src:
        n = len(src)
        for j, tok in enumerate(toks):
            a, b = _time_at(src, j * n / m, False), _time_at(src, (j + 1) * n / m, True)
            words.append(Word(text=tok, start=round(a, 3), end=round(max(b, a + 0.05), 3)))
    else:
        total, t = sum(len(x) + 1 for x in toks), seg.start
        for tok in toks:
            d = (seg.end - seg.start) * (len(tok) + 1) / total
            words.append(Word(text=tok, start=round(t, 3), end=round(t + d, 3)))
            t += d
    return Segment(start=seg.start, end=seg.end, text=new_text, words=words)


def native_transcript(code: str, edl: EDL, jd: Path) -> Transcript:
    f = jd / "analysis" / f"transcript.native.{code}.json"
    cached = _load(f, None)
    if cached is not None:
        return cached
    tr = transcription.transcribe_native(Path(edl.source_media.path), jd / "work", edl.source_media.duration, code)
    _save(f, None, tr)
    return tr


def translated_transcript(base: Transcript, target: str, source: Optional[str], jd: Path) -> Transcript:
    f = jd / "analysis" / f"transcript.to.{target}.json"
    key = _hash(base) + ":" + (source or "")
    cached = _load(f, key)
    if cached is not None:
        return cached
    segs = base.segments
    new = translate.translate_texts([s.text for s in segs], target, source)
    tr = Transcript(language=target, duration=base.duration, segments=[retime(s, t) for s, t in zip(segs, new)])
    _save(f, key, tr)
    return tr


def get_transcript(lang: str, base: Transcript, edl: EDL, jd: Path) -> Transcript:
    src = (base.language or "").lower()[:2]
    text = _text(base)
    if not base.segments:
        return base
    if lang == src:                                              # same language: only the script may be wrong
        if script_of(lang) == "latin" or has_script(text, script_of(lang)):
            return base
        try:
            return native_transcript(lang, edl, jd)
        except Exception:                                        # no Whisper: transliterate the Roman text instead
            return translated_transcript(base, lang, None, jd)
    if src in BY_CODE and script_of(src) != "latin" and not has_script(text, script_of(src)):
        try:                                                     # Roman Hinglish -> translate from clean Devanagari instead
            base = native_transcript(src, edl, jd)
        except Exception:
            pass
    return translated_transcript(base, lang, src or None, jd)


def apply_language(edl: EDL, an, jd: Path) -> Optional[str]:
    """Rebuild edl.captions in the chosen language. Returns a warning text for the user when it could not be done."""
    cs = edl.caption_settings
    lang = normalize(cs.language)
    if not lang or not cs.enabled or (edl.requirements or {}).get("caption_script"):
        return None
    name = BY_CODE[lang]["name"]
    try:
        tr = get_transcript(lang, an.transcript, edl, jd)
    except Exception as e:
        reason = str(e)[:150]
        return (f"Captions {name} mein nahi ban paye ({reason}). Internet / Whisper check karo - "
                f"abhi captions original language mein lage hain.")
    edl.captions = build_captions(edl.clips, tr, cs)
    edl.explanation.append(f"Captions language: {name}")
    return None