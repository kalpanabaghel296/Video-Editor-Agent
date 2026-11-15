"""Best-take selection ("the agent picks the best of several takes").

A speaker often says the same line 2-3 times until it comes out right. The old rule only compared two
CONSECUTIVE sentences and always dropped the earlier one. This module

  1. finds *groups of takes*: sentences that say (nearly) the same thing, even when other material is
     between them, or when a take was abandoned half-way ("So today I will talk about ... / So today I
     will talk about AI agents"),
  2. scores every take (fluency, completeness, pace, audio level, and a small bonus for later takes -
     people stop recording once they are happy), and
  3. KEEPS THE BEST take and returns cut ranges for the others.

Pure python (numpy only for the optional audio level). It never touches ffmpeg unless an audio file is given.
"""
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "ah", "aah", "hmm", "mmm"}
STOP = {"the", "a", "an", "i", "we", "you", "it", "is", "and", "to", "of", "in", "that", "this", "so", "but",
        "my", "me", "can", "will", "are", "was", "be", "have", "has", "do", "yeh", "ye", "hai", "ki", "ke",
        "ka", "ko", "mein", "main", "toh", "aur", "koi", "bhi", "jo", "woh", "hoon", "hun", "kar", "ek", "for",
        "on", "with", "at", "as", "or", "if", "then", "just", "like", "really"}
LEGIT_REPEAT = {"very", "really", "no", "yes", "bye", "so", "now", "many", "much", "more", "ha", "haha", "hello"}

SIM_THRESHOLD = 0.62      # token-level similarity that makes two sentences "the same line"
GROUP_WINDOW = 90.0       # seconds between two takes of the same line
MIN_TOKENS = 3
IDEAL_RATE = 2.6          # words / second of comfortable speech
W = {"complete": 0.30, "fluency": 0.36, "pace": 0.10, "energy": 0.14, "order": 0.10}


@dataclass
class Unit:
    idx: int
    start: float
    end: float
    text: str
    words: List[Tuple[str, float, float]] = field(default_factory=list)     # (word, start, end)
    energy_db: Optional[float] = None

    @property
    def tokens(self) -> List[str]:
        src = [w[0] for w in self.words] if self.words else self.text.split()
        return [t for t in (_norm(x) for x in src) if t]


@dataclass
class TakeGroup:
    members: List[int]                       # unit idx, in time order
    best: int
    scores: Dict[int, float]
    details: Dict[int, dict]
    cuts: List[Tuple[float, float, int]]     # (start, end, unit idx) of the dropped takes
    reasons: Dict[int, str]


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", (w or "").lower())


def units_from_transcript(tr) -> List[Unit]:
    """Duck-typed: works with models.transcript.Transcript (segments[].start/end/text/words[].text/start/end)."""
    out: List[Unit] = []
    for sg in tr.segments:
        if sg.end <= sg.start or not (sg.text or "").strip():
            continue
        words = [(w.text, w.start, w.end) for w in (sg.words or []) if _norm(w.text)]
        out.append(Unit(idx=len(out), start=float(sg.start), end=float(sg.end), text=sg.text.strip(), words=words))
    return out


def _content(tokens: Sequence[str]) -> List[str]:
    return [t for t in tokens if t not in FILLERS]


def similarity(a: Sequence[str], b: Sequence[str]) -> float:
    a, b = _content(a), _content(b)
    if len(a) < MIN_TOKENS or len(b) < MIN_TOKENS:
        return 0.0
    r = SequenceMatcher(None, a, b, autojunk=False).ratio()
    k, common = min(len(a), len(b)), 0
    while common < k and a[common] == b[common]:
        common += 1
    if common >= 3 and common >= 0.8 * k:                  # one take is the (abandoned) beginning of the other
        return max(r, 0.85)
    ca, cb = {t for t in a if t not in STOP}, {t for t in b if t not in STOP}
    if ca and cb and len(ca & cb) / len(ca | cb) < 0.34:   # same filler words are not the same sentence
        return min(r, SIM_THRESHOLD - 0.01)
    return r


def find_groups(units: List[Unit], threshold: float = SIM_THRESHOLD, window: float = GROUP_WINDOW) -> List[List[int]]:
    parent = list(range(len(units)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    toks = [u.tokens for u in units]
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            if units[j].start - units[i].end > window:
                break
            if similarity(toks[i], toks[j]) >= threshold:
                parent[find(j)] = find(i)
    groups: Dict[int, List[int]] = {}
    for i in range(len(units)):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for g in groups.values() if len(g) >= 2]


def _stats(u: Unit) -> dict:
    toks = u.tokens
    fill = sum(1 for t in toks if t in FILLERS)
    real = [t for t in toks if t not in FILLERS]
    stut = sum(1 for a, b in zip(real, real[1:]) if a == b and a not in LEGIT_REPEAT)
    stut += sum(1 for a, b in zip(real, real[1:]) if len(a) <= 3 and b.startswith(a) and len(b) > len(a) + 1 and a not in STOP)
    pauses = 0
    for (_, _, e1), (_, s2, _) in zip(u.words, u.words[1:]):
        if s2 - e1 >= 0.7:
            pauses += 1
    rate = len(real) / max(u.end - u.start, 0.2)
    return {"words": len(real), "fillers": fill, "stutters": stut, "pauses": pauses, "rate": round(rate, 2),
            "energy_db": u.energy_db}


def score_group(units: List[Unit], members: List[int]) -> Tuple[Dict[int, float], Dict[int, dict]]:
    det = {i: _stats(units[i]) for i in members}
    max_words = max(d["words"] for d in det.values()) or 1
    have_energy = all(units[i].energy_db is not None for i in members)
    max_e = max((units[i].energy_db for i in members), default=0.0) if have_energy else 0.0
    n = len(members)
    scores: Dict[int, float] = {}
    for pos, i in enumerate(members):
        d = det[i]
        ratio = d["words"] / max_words
        toks_i = _content(units[i].tokens)
        abandoned = any(j != i and len(toks_i) < len(_content(units[j].tokens)) and
                        _content(units[j].tokens)[:len(toks_i)] == toks_i for j in members)
        parts = {
            "complete": (ratio ** 2) * (0.4 if abandoned else 1.0),   # a take that stops half-way is not a candidate
            "fluency": max(0.0, 1 - 0.12 * d["fillers"] - 0.15 * d["stutters"] - 0.12 * d["pauses"]),
            "pace": 1 - min(1.0, abs(d["rate"] - IDEAL_RATE) / 2.0),
            "order": pos / (n - 1) if n > 1 else 1.0,
        }
        if have_energy:
            parts["energy"] = 1 - min(1.0, max(0.0, max_e - units[i].energy_db) / 9.0)
        wsum = sum(W[k] for k in parts)
        scores[i] = round(sum(W[k] * v for k, v in parts.items()) / wsum, 3)
        d["parts"] = {k: round(v, 2) for k, v in parts.items()}
    return scores, det


def _why(best: dict, other: dict) -> str:
    bits = []
    if best["fillers"] < other["fillers"]:
        bits.append("fewer fillers")
    if best["stutters"] < other["stutters"]:
        bits.append("no stutters" if best["stutters"] == 0 else "fewer stutters")
    if best["pauses"] < other["pauses"]:
        bits.append("fewer hesitations")
    if best["words"] > other["words"]:
        bits.append("more complete")
    if (best.get("parts", {}).get("pace", 0) - other.get("parts", {}).get("pace", 0)) > 0.15:
        bits.append("steadier pace")
    if best.get("parts", {}).get("energy", 0) - other.get("parts", {}).get("energy", 0) > 0.15:
        bits.append("clearer / louder audio")
    return ", ".join(bits) or "best overall score"


def choose_best_takes(units: List[Unit], threshold: float = SIM_THRESHOLD) -> List[TakeGroup]:
    out: List[TakeGroup] = []
    by_idx = {u.idx: u for u in units}
    for members in find_groups(units, threshold):
        scores, det = score_group(units, members)
        best = max(members, key=lambda i: (scores[i], i))            # tie -> the later take
        cuts, reasons = [], {}
        n = len(members)
        for pos, i in enumerate(members, 1):
            if i == best:
                continue
            u = by_idx[i]
            nxt = by_idx.get(i + 1)
            end = min((nxt.start - 0.05) if nxt else u.end + 0.35, u.end + 0.35)
            start, end = max(u.start - 0.02, 0.0), max(end, u.end)
            cuts.append((round(start, 3), round(end, 3), i))
            bpos = members.index(best) + 1
            reasons[i] = (f"take {pos}/{n} dropped, take {bpos}/{n} kept ({_why(det[best], det[i])}; "
                          f"score {scores[best]:.2f} vs {scores[i]:.2f}): “{u.text[:45]}”")
        out.append(TakeGroup(members=members, best=best, scores=scores, details=det, cuts=cuts, reasons=reasons))
    return out


# ---------------------------------------------------------------------------------------------
def measure_energy(audio: Path, spans: Sequence[Tuple[float, float]]) -> List[Optional[float]]:
    """RMS level (dBFS) of each (start, end) span, or None when it cannot be measured."""
    try:
        import numpy as np
        from ..utils.ffmpeg import run_bytes
    except Exception:
        return [None] * len(spans)
    res: List[Optional[float]] = []
    for s, e in spans:
        try:
            raw = run_bytes(["ffmpeg", "-v", "error", "-ss", f"{max(s, 0):.3f}", "-t", f"{max(e - s, 0.1):.3f}",
                             "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"])
            x = np.frombuffer(raw, dtype=np.int16).astype("float64") / 32768.0
            res.append(round(20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-6)), 2) if x.size else None)
        except Exception:
            res.append(None)
    return res


def attach_energy(units: List[Unit], idxs: Sequence[int], audio: Optional[Path], cache: Optional[Path] = None) -> None:
    """Measure the audio level of the given units (cached in a json file keyed by start-end)."""
    if audio is None or not Path(audio).exists():
        return
    memo: Dict[str, Optional[float]] = {}
    if cache and cache.exists():
        try:
            memo = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            memo = {}
    todo = [i for i in idxs if f"{units[i].start:.2f}-{units[i].end:.2f}" not in memo]
    if todo:
        vals = measure_energy(Path(audio), [(units[i].start, units[i].end) for i in todo])
        for i, v in zip(todo, vals):
            memo[f"{units[i].start:.2f}-{units[i].end:.2f}"] = v
        if cache:
            try:
                cache.write_text(json.dumps(memo), encoding="utf-8")
            except Exception:
                pass
    for i in idxs:
        units[i].energy_db = memo.get(f"{units[i].start:.2f}-{units[i].end:.2f}")


def analyze(tr, audio: Optional[Path] = None, cache: Optional[Path] = None) -> Tuple[List[Unit], List[TakeGroup]]:
    units = units_from_transcript(tr)
    groups_idx = find_groups(units)
    if audio is not None and groups_idx:
        attach_energy(units, sorted({i for g in groups_idx for i in g}), audio, cache)
    return units, choose_best_takes(units)


def describe(units: List[Unit], groups: List[TakeGroup]) -> List[dict]:
    """JSON for the UI."""
    res = []
    for g in groups:
        res.append({"line": units[g.members[0]].text[:80], "kept": g.best,
                    "takes": [{"index": i, "start": units[i].start, "end": units[i].end, "text": units[i].text,
                               "score": g.scores[i], "kept": i == g.best,
                               "fillers": g.details[i]["fillers"], "stutters": g.details[i]["stutters"],
                               "pauses": g.details[i]["pauses"], "rate": g.details[i]["rate"]} for i in g.members]})
    return res
