"""Built-in royalty-free background music, synthesized offline (no download, no copyright).

Creates a seamless 4-bar loop: warm chord pad + bass + soft arpeggio (+ kick / hat for upbeat styles).
styles: calm (76 bpm, no drums) | neutral (92 bpm, light beat) | fast (112 bpm, driving beat)."""
import wave
from pathlib import Path

import numpy as np

SR = 44100
_PROG = {   # per chord: (bass midi, chord midi notes)
    "calm":    [(45, [57, 60, 64]), (41, [53, 57, 60]), (48, [60, 64, 67]), (43, [55, 59, 62])],      # Am F C G
    "neutral": [(45, [57, 60, 64]), (41, [53, 57, 60]), (48, [60, 64, 67]), (43, [55, 59, 62])],
    "fast":    [(48, [60, 64, 67]), (43, [55, 59, 62]), (45, [57, 60, 64]), (41, [53, 57, 60])],      # C G Am F
}
_BPM = {"calm": 76, "neutral": 92, "fast": 112}


def _f(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _add(buf: np.ndarray, sig: np.ndarray, start: int, pan: float = 0.0) -> None:
    """add a mono signal into the stereo loop buffer at sample `start`, wrapping around the end (seamless loop)"""
    l, r = sig * (1 - max(pan, 0)), sig * (1 + min(pan, 0))
    n = len(sig)
    idx = (np.arange(n) + start) % buf.shape[1]
    np.add.at(buf[0], idx, l)
    np.add.at(buf[1], idx, r)


def make_track(out: Path, style: str = "neutral") -> Path:
    style = style if style in _PROG else "neutral"
    rng = np.random.default_rng(7)
    beat = 60.0 / _BPM[style]
    bar = int(SR * beat * 4)
    buf = np.zeros((2, bar * 4), np.float64)
    for ci, (bass, chord) in enumerate(_PROG[style]):
        b0 = ci * bar
        # --- pad: detuned sines with slow attack / release
        n = int(bar + SR * 0.6)
        t = np.arange(n) / SR
        env = np.minimum(t / 0.25, 1.0) * np.minimum((n / SR - t) / 0.5, 1.0)
        for k, note in enumerate(chord):
            fr = _f(note)
            tone = sum(np.sin(2 * np.pi * (fr + d) * t) for d in (-0.4, 0.0, 0.4)) / 3
            tone += 0.25 * np.sin(2 * np.pi * 2 * fr * t)
            _add(buf, tone * env * 0.075, b0, pan=(-0.35, 0.0, 0.35)[k])
        # --- bass on every beat (half notes when calm)
        steps = 2 if style == "calm" else 4
        for s in range(steps):
            L = int(SR * beat * (2 if style == "calm" else 1))
            tt = np.arange(L) / SR
            _add(buf, np.sin(2 * np.pi * _f(bass) * tt) * np.exp(-2.6 * tt) * 0.42, b0 + int(s * L))
        # --- arpeggio plucks
        per = 2 if style != "fast" else 4                      # notes per beat
        pat = [0, 1, 2, 1, 2, 1, 0, 2] if style != "fast" else [0, 1, 2, 1, 2, 1, 2, 1]
        for s in range(4 * per):
            note = chord[pat[s % len(pat)]] + 12
            L = int(SR * 0.45)
            tt = np.arange(L) / SR
            fr = _f(note)
            pl = (np.sin(2 * np.pi * fr * tt) + 0.4 * np.sin(2 * np.pi * 2 * fr * tt) + 0.15 * np.sin(2 * np.pi * 3 * fr * tt))
            _add(buf, pl * np.exp(-8 * tt) * 0.16, b0 + int(s * beat / per * SR), pan=0.4 if s % 2 else -0.4)
        # --- drums
        if style != "calm":
            kicks = [0, 2] if style == "neutral" else [0, 1, 2, 3]
            for kb in kicks:
                L = int(SR * 0.35)
                tt = np.arange(L) / SR
                ph = 2 * np.pi * (45 * tt + 75 * (1 - np.exp(-22 * tt)) / 22)
                _add(buf, np.sin(ph) * np.exp(-11 * tt) * 0.55, b0 + int(kb * beat * SR))
            for hb in range(4):
                L = int(SR * 0.08)
                nz = np.diff(rng.standard_normal(L + 1))
                _add(buf, nz * np.exp(-55 * np.arange(L) / SR) * (0.05 if style == "neutral" else 0.08),
                     b0 + int((hb + 0.5) * beat * SR), pan=0.2)
    buf /= max(np.abs(buf).max(), 1e-6) / 0.85
    pcm = (buf.T * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out