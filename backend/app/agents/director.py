"""UNDERSTAND stage: natural-language instruction -> structured Requirements.

Rule-based parser by default (fast, offline, deterministic). If LLM_PROVIDER is set
(ollama / anthropic) the LLM is asked for strict JSON, validated through Pydantic,
and we silently fall back to the rule parser on any failure.
"""
import json
import re
from typing import List, Literal, Optional, Tuple
import httpx
from pydantic import BaseModel, Field, ValidationError
from .. import config
from ..models.metadata import VideoMetadata


class ScriptLine(BaseModel):
    """A caption line the user dictated in the prompt (times are on the OUTPUT timeline)."""
    start: float
    end: float
    text: str


class Requirements(BaseModel):
    caption_script: List[ScriptLine] = Field(default_factory=list)
    sfx: bool = False
    preserve_timeline: bool = False       # enhance only (noise, colour, music, captions): never drop a frame
    smart_cuts: bool = True               # brain: cut stutters / false starts / retakes
    select_mode: Literal["best", "head"] = "best"   # best = highlight the best parts, head = keep from the start
    denoise_level: Literal["normal", "strong"] = "normal"
    music_style: Literal["neutral", "calm", "fast"] = "neutral"
    target_duration: Optional[float] = None
    target_start: Optional[float] = None
    target_end: Optional[float] = None
    aspect_ratio: Literal["16:9", "9:16", "1:1", "original"] = "original"
    target_width: Optional[int] = None
    target_height: Optional[int] = None
    platform: Optional[str] = None
    caption_required: bool = True
    music_required: bool = False
    style: Literal["neutral", "fast", "calm"] = "neutral"
    remove_silence: bool = True
    remove_fillers: bool = True
    zoom: bool = False
    transitions: bool = False
    fit: Literal["crop", "pad"] = "crop"
    denoise: bool = False
    color_grade: bool = False
    cut_bloopers: bool = False
    title: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    source: str = "rules"


_PLATFORMS = [  # (regex, platform, aspect)
    (r"youtube\s*shorts?|\bshorts?\b", "shorts", "9:16"),
    (r"\breels?\b|instagram\s*reels?", "reels", "9:16"),
    (r"tik\s*tok", "tiktok", "9:16"),
    (r"instagram\s*(post|feed)", "instagram_post", "1:1"),
    (r"linkedin", "linkedin", "1:1"),
    (r"youtube", "youtube", "16:9"),
]


_TC = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:\.\d+)?)"          # h:mm:ss  |  mm:ss  |  m:ss.s
_DASH = r"(?:-|–|—|\bto\b|\bse\b|\buntil\b)"


def _tc_seconds(h: Optional[str], m: str, s: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + float(s)


def parse_caption_script(instruction: str) -> Tuple[List[ScriptLine], str]:
    """Pull lines like `00:03 – 00:07: "Some text"` out of the prompt.

    Returns (lines, instruction_without_those_lines) so the timestamps in the script are not
    mistaken for the trim window and the quoted text is not mistaken for topic keywords."""
    lines: List[ScriptLine] = []
    spans = []
    quoted = re.compile(_TC + r"\s*" + _DASH + r"\s*" + _TC + r"\s*:\s*[\"“”']([^\"“”]+?)[\"“”']")
    for m in quoted.finditer(instruction):
        g = m.groups()
        s_, e_ = _tc_seconds(*g[0:3]), _tc_seconds(*g[3:6])
        if e_ > s_ and g[6].strip():
            lines.append(ScriptLine(start=round(s_, 3), end=round(e_, 3), text=g[6].strip()))
            spans.append(m.span())
    if not lines:                                          # unquoted, one per line
        plain = re.compile(r"^[ \t]*" + _TC + r"\s*" + _DASH + r"\s*" + _TC + r"\s*:\s*(.+?)\s*$", re.M)
        for m in plain.finditer(instruction):
            g = m.groups()
            s_, e_ = _tc_seconds(*g[0:3]), _tc_seconds(*g[3:6])
            if e_ > s_:
                lines.append(ScriptLine(start=round(s_, 3), end=round(e_, 3), text=g[6].strip()))
                spans.append(m.span())
    rest, last = [], 0
    for a, b in sorted(spans):
        rest.append(instruction[last:a])
        last = b
    rest.append(instruction[last:])
    return sorted(lines, key=lambda x: x.start), " ".join(rest)


def _parse_time_range(t: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """-> (duration, start, end). Understands 0:00-0:15, 00:00:00 to 00:00:15, 'first 15 seconds',
    'to 0:15', '15 seconds', '1.5 min'."""
    m = re.search(_TC + r"\s*" + _DASH + r"\s*" + _TC, t)
    if m:
        g = m.groups()
        s_, e_ = _tc_seconds(*g[0:3]), _tc_seconds(*g[3:6])
        if e_ > s_:
            return (round(e_ - s_, 3), round(s_, 3), round(e_, 3))
    m = re.search(r"(?:\bto\b|\buntil\b)\s+" + _TC, t)
    if m:
        e_ = _tc_seconds(*m.groups())
        if e_ > 0:
            return (round(e_, 3), 0.0, round(e_, 3))
    unit = r"(seconds?|secs?|sec|s|minutes?|mins?|min|सेकंड|मिनट)(?![a-z])"
    m = re.search(r"(?:\bfirst\b|\bpehle\b|\bshuru\s*ke\b|\bstarting\b)\s+(\d+(?:\.\d+)?)\s*[- ]?\s*" + unit, t)
    if m:
        v, u = float(m.group(1)), m.group(2)
        d = v * 60 if u.startswith(("min", "मिनट")) else v
        return (round(d, 3), 0.0, round(d, 3))
    m = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?\s*" + unit, t)
    if m:
        v, u = float(m.group(1)), m.group(2)
        dur = v * 60 if u.startswith(("min", "मिनट")) else v
        return (round(dur, 3), None, None)
    return (None, None, None)


def _duration(t: str) -> Optional[float]:
    dur, _, _ = _parse_time_range(t)
    return dur


def parse_rules(instruction: str) -> Requirements:
    script, instruction = parse_caption_script(instruction)
    t = instruction.lower()
    r = Requirements()
    r.caption_script = script
    dur, start_t, end_t = _parse_time_range(t)
    m_rm = re.search(r"(?:cut|remove|delete|skip|drop|chop|trim off)\s+(?:out\s+)?(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b", t)
    if m_rm:                                               # "cut the first 10 seconds" = throw them away
        dur, start_t, end_t = None, float(m_rm.group(1)), None
    r.target_duration = dur
    r.target_start = start_t
    r.target_end = end_t
    if script and dur is None:                              # no explicit length: script defines it
        r.target_duration = round(script[-1].end, 3)
    if start_t is not None and end_t is not None:           # explicit trim window = "cut exactly this part"
        r.remove_silence = False
        r.remove_fillers = False
    r.sfx = bool(re.search(r"\bsfx\b|sound effects?|whoosh|\bpop\b|\bding\b|chime|mouse click|notification", t))

    for pat, name, aspect in _PLATFORMS:
        if re.search(pat, t):
            r.platform, r.aspect_ratio = name, aspect
            break
    if re.search(r"\b(vertical|portrait)\b", t):
        r.aspect_ratio = "9:16"
    elif re.search(r"\b(landscape|widescreen)\b", t):
        r.aspect_ratio = "16:9"
    elif re.search(r"\bsquare\b", t):
        r.aspect_ratio = "1:1"
    for ar in ("9:16", "16:9", "1:1"):                      # explicit ratio always wins
        if ar in t:
            r.aspect_ratio = ar

    # explicit resolution check, e.g. 1080x1920 or 1920x1080
    res_m = re.search(r"(\d{3,4})\s*[xX*×]\s*(\d{3,4})", instruction)
    if res_m:
        w_val, h_val = int(res_m.group(1)), int(res_m.group(2))
        r.target_width, r.target_height = w_val // 2 * 2, h_val // 2 * 2
        if w_val < h_val:
            r.aspect_ratio = "9:16"
        elif w_val > h_val:
            r.aspect_ratio = "16:9"
        elif w_val == h_val:
            r.aspect_ratio = "1:1"

    neg_cap = re.search(r"(no|without|bina|mat)\s+(captions?|subtitles?)", t)
    if neg_cap:
        r.caption_required = False
    else:
        r.caption_required = True
    r.music_required = bool(re.search(r"music|bgm|soundtrack|background (track|song|score)|lo-?fi|\bbeats?\b", t)) and \
        not re.search(r"(no|without|bina)\s+(background\s+)?(music|bgm)", t)
    if re.search(r"upbeat|energetic|energy|fast|pump|hype|party|happy", t):
        r.music_style = "fast"
    elif re.search(r"calm|chill|soft|relax|lo-?fi|soothing|peaceful|slow|emotional|cinematic", t):
        r.music_style = "calm"

    if re.search(r"fast|energetic|punchy|quick|dynamic|snappy", t):
        r.style = "fast"
    elif re.search(r"calm|slow|relax|soft|cinematic", t):
        r.style = "calm"
    r.zoom = bool(re.search(r"zoom|punch|close-?up|medium close|centered|speaker in a", t)) or r.style == "fast"
    r.transitions = bool(re.search(r"transition|fade", t))
    if re.search(r"letterbox|black bars|\bpad(ding)?\b|no crop", t):
        r.fit = "pad"
    if re.search(r"(remove|cut|trim out|delete)\s+(the\s+)?(all\s+)?(silence|dead air|pauses)", t):
        r.remove_silence = True
    if re.search(r"(remove|cut|delete)\s+(the\s+)?(all\s+)?(fillers?|umm?s?|uhh?s?)", t):
        r.remove_fillers = True
    if re.search(r"keep (the )?silence|don'?t remove silence", t):
        r.remove_silence = False
    if re.search(r"keep (the )?fillers?|don'?t remove fillers?", t):
        r.remove_fillers = False

    r.denoise = bool(re.search(
        r"noise|noisy|denois|\bhiss|\bhum\b|\bbuzz|\bstatic\b|disturb|echo|clean(?:\s*up)?\s+(?:the\s+)?audio|audio\s+clean|\bshor\b|awaaz\s+saaf|"
        r"chatter|ambient|isolate.*voice|voice isolat|enhance.*(voice|audio|sound)|studio-quality|"
        r"(background|irrelevant|unwanted|extra)\s+(sounds?|voices?|talk\w*|noises?)|people\s+(talking|speaking)|traffic|crowd|fan\s+noise", t)) and \
        not re.search(r"(no|don'?t|without)\s+(remove\s+)?(background\s+)?noise\s*(removal|reduction)", t)
    r.denoise_level = "strong" if r.denoise and re.search(
        r"strong|heavy|aggressive|complete|fully|totally|all\s+(the\s+)?(noise|disturbance|background)|disturb|chatter|crowd|traffic|"
        r"people\s+(talking|speaking)|background\s+(voices?|talk)|irrelevant|unwanted", t) else "normal"
    r.color_grade = bool(re.search(r"color|colour|cinematic|lighting|glare|warm.*tones|skin tones|grade|grading", t))
    r.cut_bloopers = bool(re.search(r"blooper|giggle|awkward|false start|retake", t))

    tm = re.search(r"(?:title|heading|intro text)(?:\s+card)?\s*(?:as|:|-|=)?\s*[\"“'‘]([^\"”'’]{1,80})[\"”'’]", instruction, re.I) or \
        re.search(r"(?:title|heading)\s*(?:as|:|-|=)\s*([^.,;\n]{1,60})", instruction, re.I)
    if tm:
        r.title = tm.group(1).strip()

    # --- what kind of edit is this? -------------------------------------------------------------
    explicit_window = start_t is not None and end_t is not None
    asks_cut = bool(re.search(
        r"(remove|cut|trim out|delete|skip|drop)\s+(the\s+)?(all\s+)?(long\s+)?(silence|pauses?|dead air|fillers?|fumbl\w*|stutter\w*|"
        r"mistakes?|retakes?|bloopers?|awkward|repetition|repeat\w*)|tighten|jump ?cuts?|shorten|make it (shorter|tighter|crisp)|"
        r"\breels?\b|\bshorts?\b|tik\s*tok|highlights?|summar|trim|\bcut\b|first \d|snappy|punchy|edit.*(properly|professionally)", t))
    wants_len = r.target_duration is not None
    if (r.denoise or r.music_required or r.color_grade) and not (asks_cut or wants_len or explicit_window):
        r.preserve_timeline = True                         # "remove the noise" must never delete frames
    elif not (asks_cut or wants_len or explicit_window or r.platform or r.style == "fast"):
        r.preserve_timeline = bool(re.search(r"caption|subtitle|volume|louder|brighten|grade|colou?r", t))
    if r.preserve_timeline:
        r.remove_silence = r.remove_fillers = r.smart_cuts = False
    elif explicit_window and not asks_cut:
        r.smart_cuts = False
    if re.search(r"keep (the )?(mistakes|stutters?|retakes?)|don'?t remove (the )?(mistakes|stutters?)", t):
        r.smart_cuts = False
    if wants_len and not explicit_window:
        wants_best = re.search(r"best|highlight|reel|\bshorts?\b|tik\s*tok|summar|key (moments|points)|hook|viral|engag|teaser|trailer|"
                               r"make|create|generate|clip|video", t) and not re.search(r"\btrim\b|first \d|\bcut (it |the video )?(to|down)", t)
        r.select_mode = "best" if wants_best else "head"

    stop = {"the", "a", "an", "and", "of", "on", "in", "to", "for", "with", "my", "this", "video",
            "her", "his", "its", "their", "was", "were", "are", "is", "am", "she", "he", "you",
            "all", "best", "previous", "like", "out", "into", "clip", "strictly", "timeline", "clear"}
    blooper_words = {"blooper", "bloopers", "giggle", "giggles", "awkward", "pauses", "pause",
                     "filler", "fillers", "umm", "aah", "noise", "chatter"}
    kws: List[str] = []

    # Quoted text (e.g. self-introduction 'Hello, my name is Riddhi...')
    for q in re.findall(r"['\"“]([^'\"”]{3,120})['\"”]", instruction):
        for w in re.findall(r"[\w']+", q.lower()):
            if w not in stop and w not in blooper_words and len(w) > 2 and w not in kws:
                kws.append(w)

    # Introduction cues
    if re.search(r"self-introduction|introduction|\bintro\b", t):
        for w in ["hello", "name", "introduction", "student"]:
            if w not in kws:
                kws.append(w)

    # Focus / topic patterns
    m = re.search(r"(?:about|focus(?:ing)? on|highlight|regarding|around|only|best .* of|to the)\s+([^.,;\n()]+)", t)
    if m:
        for w in re.findall(r"[\w']+", m.group(1)):
            if w not in stop and w not in blooper_words and len(w) > 2 and w not in kws:
                kws.append(w)

    r.keywords = kws[:8]
    return r


_PROMPT = """You convert a video-editing instruction into JSON. Reply with ONLY a JSON object, no prose.
Schema: {"target_duration": number|null (seconds), "aspect_ratio": "16:9"|"9:16"|"1:1"|"original",
"platform": string|null, "caption_required": bool, "music_required": bool,
"style": "neutral"|"fast"|"calm", "remove_silence": bool, "remove_fillers": bool, "zoom": bool,
"transitions": bool, "fit": "crop"|"pad", "denoise": bool, "color_grade": bool, "cut_bloopers": bool,
"sfx": bool (sound effects requested), "title": string|null (title card text), "keywords": [string]}
Video info: duration=%.1fs, size=%dx%d.
Instruction: %s"""


def _llm_json(prompt: str) -> Optional[str]:
    try:
        if config.LLM_PROVIDER == "ollama":
            r = httpx.post(f"{config.OLLAMA_URL}/api/generate", timeout=90,
                           json={"model": config.OLLAMA_MODEL, "prompt": prompt, "format": "json", "stream": False})
            return r.json().get("response")
        if config.LLM_PROVIDER == "anthropic":
            import os
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                return None
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
                           headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                           json={"model": config.ANTHROPIC_MODEL, "max_tokens": 500,
                                 "messages": [{"role": "user", "content": prompt}]})
            return "".join(b.get("text", "") for b in r.json().get("content", []))
    except Exception:
        return None
    return None


def understand(instruction: str, meta: VideoMetadata) -> Requirements:
    if config.LLM_PROVIDER != "none":
        raw = _llm_json(_PROMPT % (meta.duration, meta.width, meta.height, instruction))
        if raw:
            try:
                data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
                req = Requirements(**{k: v for k, v in data.items()
                                      if k in Requirements.model_fields and k not in ("caption_script",)})
                rules = parse_rules(instruction)               # exact times / dictated captions are never left to the LLM
                req.caption_script = rules.caption_script
                for f in ("target_start", "target_end"):
                    if getattr(rules, f) is not None:
                        setattr(req, f, getattr(rules, f))
                if rules.target_start is not None and rules.target_end is not None:
                    req.target_duration = rules.target_duration
                    req.remove_silence, req.remove_fillers = rules.remove_silence, rules.remove_fillers
                req.sfx = req.sfx or rules.sfx
                req.source = config.LLM_PROVIDER
                return req
            except (ValidationError, AttributeError, ValueError):
                pass
    return parse_rules(instruction)