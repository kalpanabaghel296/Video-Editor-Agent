"""Week 2 / Day 3 — basic filler word flagging from the transcript."""
import re
from typing import List
from ..models.analysis import Filler
from ..models.transcript import Transcript

HARD = {"um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "mmm"}
SOFT = {"like", "basically", "actually", "literally", "matlab", "yaani", "toh"}
PHRASES_SOFT = ["you know", "i mean", "sort of", "kind of"]


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def detect_fillers(tr: Transcript) -> List[Filler]:
    out: List[Filler] = []
    for seg in tr.segments:
        if seg.words:
            toks = [(_norm(w.text), w.start, w.end, False) for w in seg.words]
        else:                                          # estimate timing from character length
            words = seg.text.split()
            total = sum(len(w) for w in words) or 1
            t, toks = seg.start, []
            for w in words:
                d = (seg.end - seg.start) * len(w) / total
                toks.append((_norm(w), t, t + d, True))
                t += d
        for i, (w, s, e, approx) in enumerate(toks):
            if w in HARD:
                out.append(Filler(word=w, start=round(s, 3), end=round(e, 3), kind="hard", approx=approx))
            elif w in SOFT:
                out.append(Filler(word=w, start=round(s, 3), end=round(e, 3), kind="soft", approx=approx))
            elif i + 1 < len(toks) and f"{w} {toks[i + 1][0]}" in PHRASES_SOFT:
                out.append(Filler(word=f"{w} {toks[i + 1][0]}", start=round(s, 3),
                                  end=round(toks[i + 1][2], 3), kind="soft", approx=approx))
    return out
