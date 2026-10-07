"""Week 2 / Day 1 — Whisper transcription (faster-whisper preferred, openai-whisper fallback)."""
from pathlib import Path
from .. import config
from ..models.transcript import Segment, Transcript, Word
from ..utils.ffmpeg import run

# Fix compatibility between faster-whisper and PyAV (where metadata_errors is unsupported)
try:
    import av
    _orig_av_open = av.open

    def _safe_av_open(*args, **kwargs):
        kwargs_clean = dict(kwargs)
        kwargs_clean.pop("metadata_errors", None)
        try:
            return _orig_av_open(*args, **kwargs)
        except TypeError:
            return _orig_av_open(*args, **kwargs_clean)

    av.open = _safe_av_open
except Exception:
    pass


class TranscriptionUnavailable(RuntimeError):
    pass


_MODELS: dict = {}


def extract_audio(video: Path, wav: Path) -> Path:
    wav.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-i", str(video), "-vn",
             "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "16000", str(wav)])
    except Exception:
        run(["ffmpeg", "-y", "-hide_banner", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    return wav


def _faster_whisper(wav: Path, duration: float) -> Transcript:
    from faster_whisper import WhisperModel      # type: ignore
    key = ("fw", config.WHISPER_MODEL)
    if key not in _MODELS:
        _MODELS[key] = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    kwargs = {
        "word_timestamps": True,
        "beam_size": config.WHISPER_BEAM,
        "best_of": 5,
        "temperature": [0.0, 0.2, 0.4],                   # retry hotter only when decoding fails
        "vad_filter": True,
        "vad_parameters": dict(min_silence_duration_ms=400, speech_pad_ms=250),
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 2.4,
        "no_speech_threshold": 0.6,
        "initial_prompt": config.WHISPER_PROMPT or None,
    }
    if config.WHISPER_LANGUAGE:
        kwargs["language"] = config.WHISPER_LANGUAGE
    segs, info = _MODELS[key].transcribe(str(wav), **kwargs)
    out = []
    for s in segs:
        words = [Word(text=w.word.strip(), start=float(w.start), end=float(w.end))
                 for w in (s.words or []) if w.word.strip()]
        txt = s.text.strip()
        if txt:
            out.append(Segment(start=float(s.start), end=float(s.end), text=txt, words=words))
    return Transcript(language=info.language, duration=duration, segments=out)


def _openai_whisper(wav: Path, duration: float) -> Transcript:
    import whisper                                # type: ignore
    key = ("ow", config.WHISPER_MODEL)
    if key not in _MODELS:
        _MODELS[key] = whisper.load_model(config.WHISPER_MODEL)
    kwargs = {
        "word_timestamps": True,
        "beam_size": config.WHISPER_BEAM,
        "condition_on_previous_text": False,
        "initial_prompt": config.WHISPER_PROMPT or None,
    }
    if config.WHISPER_LANGUAGE:
        kwargs["language"] = config.WHISPER_LANGUAGE
    res = _MODELS[key].transcribe(str(wav), **kwargs)
    out = []
    for s in res.get("segments", []):
        words = [Word(text=w["word"].strip(), start=float(w["start"]), end=float(w["end"]))
                 for w in s.get("words", [])]
        out.append(Segment(start=float(s["start"]), end=float(s["end"]), text=s["text"].strip(), words=words))
    return Transcript(language=res.get("language"), duration=duration, segments=out)


def transcribe(video: Path, work_dir: Path, duration: float) -> Transcript:
    mode = config.TRANSCRIBER
    if mode == "none":
        raise TranscriptionUnavailable("TRANSCRIBER=none")
    wav = extract_audio(video, work_dir / "audio.wav")
    errors = []
    for name, fn in (("faster-whisper", _faster_whisper), ("whisper", _openai_whisper)):
        if mode not in ("auto", name):
            continue
        try:
            return fn(wav, duration)
        except ImportError as e:
            errors.append(f"{name}: not installed")
        except Exception as e:                    # model download / runtime failure
            errors.append(f"{name}: {e}")
    raise TranscriptionUnavailable("; ".join(errors) or "no transcriber configured")
