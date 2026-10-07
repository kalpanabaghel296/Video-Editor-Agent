"""Audio-only noise removal (spectral gating). Never touches the video or the timeline.

Idea: learn the noise spectrum from the quietest parts of the recording, then attenuate every
time-frequency bin that is not clearly louder than that noise. Steady background noise (fan, AC,
hiss, hum, traffic rumble, room tone) is reduced a lot; speech is kept. Output has exactly the
same length as the input, so cuts / captions / sync are unaffected.
Needs only numpy (already installed with opencv / faster-whisper)."""
import wave
from pathlib import Path
from typing import Literal

import numpy as np

from ..utils.ffmpeg import run

Level = Literal["normal", "strong"]
N_FFT, HOP = 2048, 512
MAX_SECONDS = 20 * 60        # longer audio -> caller falls back to the ffmpeg filter chain


def _params(level: Level):
    # (over-subtraction, max attenuation dB, quiet-frame percentile, pause extra attenuation dB)
    return (3.0, 24.0, 15, 0.0) if level == "normal" else (4.0, 32.0, 20, 8.0)


def _stft(x: np.ndarray) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    pad = np.pad(x, (N_FFT, N_FFT + HOP), mode="reflect")
    n = 1 + (len(pad) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.fft.rfft(pad[idx] * win, axis=1).astype(np.complex64)


def _istft(S: np.ndarray, length: int) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    frames = np.fft.irfft(S, axis=1).astype(np.float32) * win
    out = np.zeros(HOP * (len(frames) - 1) + N_FFT, np.float32)
    norm = np.zeros_like(out)
    for i, f in enumerate(frames):
        out[i * HOP:i * HOP + N_FFT] += f
        norm[i * HOP:i * HOP + N_FFT] += win * win
    out /= np.maximum(norm, 1e-6)
    return out[N_FFT:N_FFT + length]


def _smooth(a: np.ndarray, kt: int, kf: int) -> np.ndarray:
    for axis, k in ((0, kt), (1, kf)):
        if k > 1:
            ker = np.ones(k, np.float32) / k
            a = np.apply_along_axis(lambda v: np.convolve(v, ker, mode="same"), axis, a)
    return a


def denoise_channel(x: np.ndarray, level: Level = "normal") -> np.ndarray:
    over, max_att_db, pct, pause_db = _params(level)
    S = _stft(x)
    P = np.abs(S) ** 2
    frame_e = P.mean(axis=1)
    k = max(int(len(frame_e) * pct / 100), min(10, len(frame_e)))
    quiet = np.argsort(frame_e)[:k]
    noise = P[quiet].mean(axis=0)
    noise = _smooth(noise[None, :], 1, 9)[0]                       # smooth noise estimate over frequency
    snr = _smooth(P, 3, 3) / (noise[None, :] * over + 1e-12)       # smoothed power -> far less random leakage
    gain = np.clip(1.0 - 1.0 / np.maximum(snr, 1e-6), 0.0, 1.0)    # Wiener-like gain
    gmin = 10 ** (-max_att_db / 20)
    gain = np.maximum(np.sqrt(gain), gmin)
    gain = _smooth(gain, 3, 3)                                      # less "musical noise"
    if pause_db:                                                    # strong: also push the pauses down
        speechy = frame_e > (noise.mean() * 6.0)
        sp = np.convolve(speechy.astype(np.float32), np.ones(9, np.float32) / 9, mode="same") > 0.05
        gain[~sp] *= 10 ** (-pause_db / 20)
    return _istft(S * gain, len(x))


def read_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        ch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return raw.reshape(-1, ch).T, sr


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    pcm = (np.clip(data.T, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(data.shape[0]); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def clean_audio(video: Path, out_wav: Path, work: Path, level: Level = "normal", duration: float = 0.0) -> Path:
    """Extract the audio of `video`, denoise it, write `out_wav` (44.1 kHz stereo, same length)."""
    if duration and duration > MAX_SECONDS:
        raise RuntimeError("audio too long for spectral denoise")
    raw = work / "audio_raw.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100",
         "-c:a", "pcm_s16le", str(raw)])
    data, sr = read_wav(raw)
    cleaned = np.stack([denoise_channel(ch, level) for ch in data])
    write_wav(out_wav, cleaned, sr)
    return out_wav