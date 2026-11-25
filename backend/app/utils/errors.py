"""Turn raw exceptions into messages a student can act on (Hinglish, short)."""
import re
from .ffmpeg import FFmpegMissing, install_hint


def friendly(e: BaseException) -> str:
    raw = str(e)
    if isinstance(e, FFmpegMissing):
        return raw
    if "WinError 2" in raw or "cannot find the file specified" in raw:
        return install_hint("ffmpeg/ffprobe", raw[:120])
    if "WinError 4551" in raw or "Application Control" in raw:
        return ("Windows Application Control ne ek program block kiya: " + raw[:160] +
                " — python -m ... se chalao ya IT policy me allow karo.")
    if re.search(r"huggingface|ConnectionError|Max retries|getaddrinfo|offline", raw, re.I):
        return ("Whisper model download nahi ho paya (internet chahiye, sirf pehli baar). "
                "Internet check karo ya WHISPER_MODEL=tiny/base try karo. [" + raw[:120] + "]")
    if "video stream" in raw.lower():
        return "Is file me video stream nahi mili — koi valid video upload karo."
    return raw
