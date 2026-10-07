"""The agent's "editor's brain": decide WHICH parts of the recording are mistakes and should be cut.

Looks at the word-level transcript, the silences and the filler words and returns a list of
Fumble(start, end, kind, reason). Pure python, no model, computed on the fly (so it always matches
the current - possibly manually corrected - transcript).

kinds
  long_pause   dead air (audio silence, or a big gap between words even if background noise hides the silence)
  hesitation   long pause in the middle of a sentence ("so ... uh ... I think")
  stutter      the same word repeated ("the the", "I I I") or a cut-off fragment ("wh- what")
  false_start  a phrase said twice in a row ("I want to I want to go") -> the first try is cut
  retake       the speaker re-recorded a sentence -> the earlier, abandoned take is cut
  filler       um / uh / hmm
"""
import re
from difflib import SequenceMatcher
from typing import List, Tuple

from ..models.analysis import Filler, Fumble, Silence
from ..models.transcript import Transcript

LONG_PAUSE = 1.0          # pauses >= this are dead air
MID_PAUSE = 0.6           # pauses >= this are shortened when style is fast / smart cut is aggressive
HESITATION_GAP = 0.8      # gap between two words of one sentence
KEEP_PAUSE = 0.30         # natural breath left after trimming a long pause
RETAKE_SIM = 0.72         # text similarity that makes two consecutive sentences a re-take
RETAKE_WINDOW = 8.0       # seconds between the two takes
FUNCTION = {"the", "a", "an", "i", "we", "you", "it", "is", "and", "to", "of", "in", "that", "this", "so", "but",
            "my", "me", "can", "will", "are", "was", "be", "have", "has", "do", "yeh", "ye", "hai", "ki", "ke",
            "ka", "mein", "main", "toh", "to", "aur", "koi", "bhi", "jo", "woh"}
LEGIT_REPEAT = {"very", "really", "no", "yes", "bye", "so", "now", "many", "much", "more", "ha", "haha", "hello"}


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def _words(tr: Transcript) -> List[Tuple[str, float, float, int]]:
    out = []
    for si, s in enumerate(tr.segments):
        if s.words:
            out += [(_norm(w.text), w.start, w.end, si) for w in s.words if _norm(w.text)]
        else:
            toks = s.text.split()
            tot = sum(len(t) for t in toks) or 1
            t = s.start
            for tok in toks:
                d = (s.end - s.start) * len(tok) / tot
                if _norm(tok):
                    out.append((_norm(tok), t, t + d, si))
                t += d
    return out


def _shorten(start: float, end: float, keep: float) -> Tuple[float, float]:
    """cut the middle of [start,end] but leave `keep` seconds (half on each side) of natural pause"""
    return start + keep / 2, end - keep / 2


def detect_fumbles(tr: Transcript, silences: List[Silence], fillers: List[Filler], duration: float,
                   aggressive: bool = False) -> List[Fumble]:
    out: List[Fumble] = []
    words = _words(tr)

    # 1. dead air from audio silence
    for s in silences:
        if s.duration >= LONG_PAUSE or (aggressive and s.duration >= MID_PAUSE):
            a, b = _shorten(s.start, s.end, KEEP_PAUSE if s.duration >= LONG_PAUSE else 0.4)
            if b - a >= 0.15:
                out.append(Fumble(start=round(a, 3), end=round(b, 3), kind="long_pause",
                                  reason=f"{s.duration:.1f}s of silence at {s.start:.1f}s"))

    # 2. pauses between words (also finds pauses that are NOT silent because of background noise)
    for (w1, s1, e1, g1), (w2, s2, e2, g2) in zip(words, words[1:]):
        gap = s2 - e1
        if gap >= LONG_PAUSE or (aggressive and gap >= MID_PAUSE):
            a, b = _shorten(e1, s2, KEEP_PAUSE)
            kind = "long_pause" if g1 != g2 or gap >= LONG_PAUSE else "hesitation"
            out.append(Fumble(start=round(a, 3), end=round(b, 3), kind=kind,
                              reason=f"{gap:.1f}s gap between “{w1}” and “{w2}” at {e1:.1f}s"))
        elif g1 == g2 and gap >= HESITATION_GAP:
            a, b = _shorten(e1, s2, 0.35)
            out.append(Fumble(start=round(a, 3), end=round(b, 3), kind="hesitation",
                              reason=f"hesitation ({gap:.1f}s) mid-sentence after “{w1}” at {e1:.1f}s"))
    # silence before the first word / after the last word of the speech
    if words:
        if words[0][1] >= LONG_PAUSE:
            out.append(Fumble(start=0.0, end=round(words[0][1] - 0.25, 3), kind="long_pause",
                              reason=f"{words[0][1]:.1f}s of nothing before the first word"))
        if duration - words[-1][2] >= LONG_PAUSE + 0.5:
            out.append(Fumble(start=round(words[-1][2] + 0.6, 3), end=round(duration, 3), kind="long_pause",
                              reason=f"{duration - words[-1][2]:.1f}s of nothing after the last word"))

    # 3. stutters: repeated words and cut-off fragments
    i = 0
    while i < len(words) - 1:
        w, s, e, g = words[i]
        w2, s2, e2, g2 = words[i + 1]
        close = s2 - e < 1.2
        is_rep = (w == w2 and close and (w in FUNCTION or len(w) <= 3 or w not in LEGIT_REPEAT) and w not in LEGIT_REPEAT)
        is_frag = (len(w) <= 3 and w2.startswith(w) and len(w2) > len(w) + 1 and close and w not in FUNCTION)
        if is_rep or is_frag:
            out.append(Fumble(start=round(max(s - 0.02, 0), 3), end=round(s2 - 0.01, 3), kind="stutter",
                              confidence=0.8 if is_rep else 0.6,
                              reason=f"stutter “{w} {w2}” at {s:.1f}s" if is_rep else f"cut-off fragment “{w}-” at {s:.1f}s"))
        i += 1

    # 4. false starts: an n-gram repeated right after itself  ("i want to i want to go")
    n_words = len(words)
    for n in range(5, 1, -1):
        for i in range(0, n_words - 2 * n + 1):
            a_ = [x[0] for x in words[i:i + n]]
            b_ = [x[0] for x in words[i + n:i + 2 * n]]
            if a_ == b_ and any(x not in FUNCTION for x in a_) and words[i + n][1] - words[i + n - 1][2] < 1.5:
                out.append(Fumble(start=round(max(words[i][1] - 0.02, 0), 3), end=round(words[i + n][1] - 0.01, 3),
                                  kind="false_start", confidence=0.85,
                                  reason=f"phrase repeated: “{' '.join(a_)}” at {words[i][1]:.1f}s (first try cut)"))

    # 5. retakes: two consecutive sentences that say (almost) the same thing -> drop the first
    segs = [s for s in tr.segments if s.end > s.start and s.text.strip()]
    for a, b in zip(segs, segs[1:]):
        ta, tb = " ".join(_norm(x) for x in a.text.split()), " ".join(_norm(x) for x in b.text.split())
        if len(ta.split()) >= 3 and b.start - a.end <= RETAKE_WINDOW and SequenceMatcher(None, ta, tb).ratio() >= RETAKE_SIM:
            out.append(Fumble(start=round(a.start, 3), end=round(max(b.start - 0.05, a.end), 3), kind="retake",
                              confidence=0.8, reason=f"re-take: “{a.text.strip()[:50]}” was said again at {b.start:.1f}s"))

    # 6. hard filler words (um / uh ...) with exact timing
    for f in fillers:
        if f.kind == "hard" and not f.approx:
            out.append(Fumble(start=round(max(f.start - 0.03, 0), 3), end=round(f.end + 0.03, 3), kind="filler",
                              reason=f"filler “{f.word}” at {f.start:.1f}s"))

    # merge overlapping cuts (keep the more specific reason = first one)
    out.sort(key=lambda x: (x.start, x.end))
    merged: List[Fumble] = []
    for f in out:
        if merged and f.start <= merged[-1].end + 0.02:
            m = merged[-1]
            m.end = max(m.end, f.end)
            if m.kind in ("long_pause", "filler") and f.kind not in ("long_pause", "filler"):
                m.kind, m.reason = f.kind, f.reason
        else:
            merged.append(f.model_copy())
    return [f for f in merged if f.end - f.start >= 0.08 and f.end <= duration + 0.01]