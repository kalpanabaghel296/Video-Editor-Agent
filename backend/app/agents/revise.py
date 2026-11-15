"""Chat-style revise: "zoom kam karo", "caption chhote", "20 second me karo", "music hata do".

A follow-up message is merged into the PREVIOUS instruction, feature group by feature group (duration, zoom,
captions, music, sound effects, noise, look, aspect/platform, framing, cuts, title). For every group the message
touches, the old wording of that group is removed from the previous instruction and replaced by one canonical
phrase, so conflicting old words can never win. The merged instruction is then run through the normal agent
(director -> planner -> renderer), so chat revise needs no extra rendering code and no LLM.

Pure python (re only). Returns the new instruction, what changed, and a short Hinglish reply.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import director as d

_DUR_STRIP = re.compile(
    r"\bfirst\s+\d+(?:\.\d+)?\s*[- ]?\s*(?:seconds?|secs?|s|minutes?|mins?)\b"
    r"|\b\d+(?:\.\d+)?\s*[- ]?\s*(?:seconds?|secs?|sec|minutes?|mins?|min)\b"
    r"|\b(?:\d{1,2}:)?\d{1,2}:\d{2}\s*(?:-|–|—|to)\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\b"
    r"|\b(?:make it\s+)?(?:shorter|longer|shorten|tighter)\b", re.I)
_ZOOM = re.compile(r"(?:\b(?:no|without|bina|less|lesser|low|subtle|slight|thoda|kam|more|extra|strong|heavy|deep|zyada|add|with)\s+)*"
                   r"\b(?:zoom(?:s|ing)?(?:[- ]?ins?)?|punch[- ]?ins?)\b(?:\s+(?:off|remove|kam|less|more|zyada|subtle))?", re.I)
_CAPS = re.compile(r"(?:\b(?:no|without|bina|add|with)\s+)*"
                   r"(?:\b(?:small|smaller|large|larger|big|bigger|hormozi|karaoke|minimal|minimalist|boxed|box|classic|bold|clean|simple|viral|"
                   r"subtle|default)\s+)*\b(?:captions?|subtitles?)\b(?:\s+(?:off|remove|mat))?", re.I)
_MUSIC = re.compile(r"(?:\b(?:no|without|bina|add|with|calm|chill|soft|fast|upbeat|energetic|lo-?fi|background)\s+)*"
                    r"\b(?:music|bgm|soundtrack)\b(?:\s+(?:off|remove|mat))?", re.I)
_SFX = re.compile(r"(?:\b(?:no|without|bina|add|with)\s+)*\b(?:sfx|sound effects?|whoosh)\b(?:\s+(?:off|remove))?", re.I)
_NOISE = re.compile(r"(?:\b(?:no|without|bina|remove|reduce|strong|heavy)\s+)*(?:\b(?:background|audio)\s+)?"
                    r"\b(?:noise|denois\w*|hiss|buzz|shor)\b(?:\s+(?:removal|reduction|off|remove))?", re.I)
_LOOK = re.compile(r"\b(?:black\s*(?:and|&|n)\s*white|b\s*&\s*w|monochrome|noir|vintage|retro|cinematic|vivid|vibrant|faded|matte|"
                   r"(?:warm|cool|warmer|cooler)\s+(?:tone|tones|look|colou?rs|filter)|(?:no|without)\s+filters?)\b", re.I)
_ASPECT = re.compile(r"\b(?:youtube\s*shorts?|yt\s*shorts?|shorts|instagram\s*(?:reels?|post|feed)|reels?|tik\s*tok|linkedin|youtube|"
                     r"vertical|portrait|landscape|widescreen|square|9:16|16:9|1:1)\b", re.I)
_FRAME = re.compile(r"[^.,;]*\b(?:blur(?:red)?\s+(?:background|bg|fill|sides?|bars?)|face|center[- ]?crop|centre[- ]?crop|no\s+track\w*)\b[^.,;]*", re.I)
_KEEP = re.compile(r"(?:keep|don'?t\s+remove)\s+(?:the\s+)?(?:silence|pauses?|fillers?|mistakes|stutters?|retakes?)", re.I)


@dataclass
class Revision:
    instruction: str
    changes: List[str] = field(default_factory=list)
    reply: str = ""
    understood: bool = True


_DANGLING = re.compile(r"\b(?:and|also|add|with|the|a|an|make it|then|plus|aur|bhi|please|pls)\b\s*(?=[.,;]|$)", re.I)


def _tidy(text: str) -> str:
    """remove the stumps left behind by deleting a phrase ("... and also add the .", "make it .")"""
    for _ in range(4):
        text = _DANGLING.sub("", text.strip())
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"(?:\s*[.,;]\s*){2,}", ". ", text)
    return text.strip(" ,.;")


def _strip(text: str, rx: "re.Pattern") -> str:
    return _tidy(rx.sub(" ", text))


_CUT_ITEMS = {
    "silence": re.compile(r"(?:\b(?:remove|cut|delete|keep|don'?t\s+remove|skip|drop)\s+(?:the\s+|all\s+)?(?:long\s+)?)?\b(?:silence|pauses?|dead air)\b", re.I),
    "fillers": re.compile(r"(?:\b(?:remove|cut|delete|keep|don'?t\s+remove|skip|drop)\s+(?:the\s+|all\s+)?)?\b(?:fillers?|ums?|uhs?)\b", re.I),
    "mistakes": re.compile(r"(?:\b(?:remove|cut|delete|keep|don'?t\s+remove|skip|drop)\s+(?:the\s+|all\s+)?)?\b(?:mistakes?|fumbl\w*|stutter\w*|retakes?|bloopers?|false\s+starts?)\b", re.I),
}


def _look_phrase(look) -> str:
    p = {"bw": "black and white", "noir": "noir", "vintage": "vintage", "cinematic": "cinematic", "vivid": "vivid colors",
         "faded": "faded matte look", "warm": "warm tone look", "cool": "cool tone look"}.get(look.preset, "")
    bits = [p] if p else []
    if look.brightness: bits.append("brighter" if look.brightness > 0 else "darker")
    if look.contrast: bits.append("more contrast" if look.contrast > 0 else "less contrast")
    if look.saturation: bits.append("more saturation" if look.saturation > 0 else "less saturation")
    if look.warmth: bits.append("warmer" if look.warmth > 0 else "cooler")
    if look.sharpen: bits.append("sharper")
    if look.vignette: bits.append("vignette")
    if look.grain: bits.append("film grain")
    if look.letterbox: bits.append("letterbox")
    if look.mirror: bits.append("mirror")
    if look.fade_in_out: bits.append("fade in and out")
    return " ".join(bits)


def interpret(message: str, prev: str, current_duration: Optional[float] = None) -> Revision:
    msg = (message or "").strip()
    m = d.normalize_text(msg.lower())
    base = (prev or "").strip()
    pr = d.parse_rules(base)             # what the instruction means NOW
    mr = d.parse_rules(m)                # what the message says
    add: List[str] = []
    ch: List[str] = []

    # ---- length --------------------------------------------------------------------------------
    dur = d._parse_time_range(m)[0] if re.search(r"\d", m) else None
    if dur:
        base = _strip(base, _DUR_STRIP)
        add.append(f"make it {dur:g} seconds")
        ch.append(f"length {dur:g} sec")
    elif re.search(r"\b(shorter|shorten|tighter|crisper|crisp)\b", m) and not re.search(r"\bcaptions?\b", m):
        if current_duration and current_duration > 6:
            new = max(5, round(current_duration * 0.8))
            base = _strip(base, _DUR_STRIP)
            add.append(f"make it {new} seconds")
            ch.append(f"{current_duration:.0f}s se chhota karke {new} sec")
        else:
            add.append("tighten and shorten, remove pauses")
            ch.append("aur tight / chhota")
    elif re.search(r"\b(longer|lamba)\b", m) and current_duration:
        new = round(current_duration * 1.25)
        base = _strip(base, _DUR_STRIP)
        add.append(f"make it {new} seconds")
        ch.append(f"{current_duration:.0f}s se lamba karke {new} sec")

    # ---- zoom ----------------------------------------------------------------------------------
    if re.search(r"zoom|punch[- ]?in", m):
        base = _strip(base, _ZOOM)
        if _neg_zoom(m):
            add.append("no zoom"); ch.append("zoom band")
        elif mr.zoom_strength == "subtle":
            add.append("subtle zoom"); ch.append("zoom kam (subtle)")
        elif mr.zoom_strength == "strong":
            add.append("strong zoom"); ch.append("zoom zyada (strong)")
        else:
            add.append("add zoom"); ch.append("zoom on")

    # ---- captions --------------------------------------------------------------------------------
    if re.search(r"\bcaptions?\b|\bsubtitles?\b", m):
        base = _strip(base, _CAPS)
        if not mr.caption_required:
            add.append("no captions"); ch.append("captions band")
        else:
            style = mr.caption_style if mr.caption_style != "auto" else pr.caption_style
            size = mr.caption_size if mr.caption_size != "normal" else (
                "normal" if re.search(r"normal\s+size|default\s+size|regular\s+size", m) else pr.caption_size)
            phrase = []
            if style != "auto":
                phrase.append(f"{style} captions")
            if size != "normal":
                phrase.append(f"{size} captions")
            add.append(", ".join(phrase) or "add captions")
            ch.append("captions: " + (", ".join(x.replace(" captions", "") for x in phrase) or "on"))

    # ---- music -----------------------------------------------------------------------------------
    if re.search(r"music|bgm|soundtrack|lo-?fi", m):
        base = _strip(base, _MUSIC)
        if not mr.music_required:
            add.append("no music"); ch.append("music band")
        else:
            style = mr.music_style if mr.music_style != "neutral" else pr.music_style
            add.append(("upbeat " if style == "fast" else "calm " if style == "calm" else "") + "background music")
            ch.append(f"music on ({style})")

    # ---- sound effects / noise -------------------------------------------------------------------
    if re.search(r"\bsfx\b|sound effects?|whoosh", m):
        base = _strip(base, _SFX)
        add.append("sound effects" if mr.sfx else "no sound effects")
        ch.append("sound effects on" if mr.sfx else "sound effects band")
    if re.search(r"noise|denois|hiss|buzz|\bshor\b|awaaz", m):
        base = _strip(base, _NOISE)
        off = bool(re.search(r"\b(?:no|don'?t|without|mat|nahi)\b[^.]*\bnoise|noise\s+(?:removal\s+)?(?:off|mat)", m))
        if off:
            add.append("don't remove noise"); ch.append("noise removal band")
        else:
            add.append("remove background noise" + (" strongly" if mr.denoise_level == "strong" else ""))
            ch.append("noise removal on")

    # ---- look ------------------------------------------------------------------------------------
    if _LOOK.search(m) or re.search(r"\b(?:brighter|darker|warmer|cooler|more contrast|less contrast|saturat\w+)\b", m):
        if re.search(r"\b(?:no|without|hata|remove)\s+filters?|original\s+colou?rs", m):
            base = _strip(base, _LOOK)
            ch.append("filter hata diya")
        else:
            base = _strip(base, _LOOK)
            add.append(_look_phrase(mr.look) or msg)
            ch.append("look: " + (mr.look.describe() or "updated"))

    # ---- aspect / platform ---------------------------------------------------------------------
    if _ASPECT.search(m):
        base = _strip(base, _ASPECT)
        add.append(_aspect_phrase(m, mr))
        ch.append(f"format: {mr.platform or mr.aspect_ratio}")

    # ---- framing ---------------------------------------------------------------------------------
    if mr.reframe != "auto":
        base = _strip(base, _FRAME)
        add.append({"face": "follow my face", "center": "center crop", "blur": "blurred background"}[mr.reframe])
        ch.append({"face": "face tracking", "center": "centre crop", "blur": "blur background"}[mr.reframe])

    # ---- cuts ------------------------------------------------------------------------------------
    items = [k for k, rx in _CUT_ITEMS.items() if rx.search(m)]
    if items:
        keep = bool(_KEEP.search(m))
        for k in items:
            base = _strip(base, _CUT_ITEMS[k])
        add.append(", ".join(("keep " if keep else "remove ") + k for k in items))
        ch.append(("rakhe: " if keep else "hataye: ") + ", ".join(items))

    # ---- title -----------------------------------------------------------------------------------
    tm = re.search(r"(?:title|heading)\s*(?:to|as|:|-|=)?\s*[\"“'‘]([^\"”'’]{1,80})[\"”'’]", msg, re.I)
    if tm:
        base = re.sub(r"(?:title|heading)(?:\s+card)?\s*(?:as|:|-|=)?\s*[\"“'‘][^\"”'’]{1,80}[\"”'’]", " ", base, flags=re.I)
        add.append(f'title "{tm.group(1)}"')
        ch.append(f"title: {tm.group(1)}")

    if not ch:                           # nothing recognised: treat the message as an extra instruction
        new = _tidy(base + ". " + msg)
        return Revision(new, [], "Exact setting samajh nahi aayi, to maine ise extra instruction ki tarah add kar diya "
                        "— re-render ho raha hai.", understood=False)
    new = _tidy(". ".join(x for x in [base.strip(" .,;")] + add if x))
    return Revision(new, ch, "Theek hai: " + "; ".join(ch) + ". Re-render ho raha hai…")


def _neg_zoom(m: str) -> bool:
    return d._neg(m, r"(?:zoom(?:s|ing)?(?:[- ]?ins?)?|punch[- ]?ins?)")


def _aspect_phrase(m: str, mr) -> str:
    if mr.platform:
        return {"reels": "instagram reel", "shorts": "youtube short", "tiktok": "tiktok", "instagram_post": "instagram post",
                "linkedin": "linkedin post", "youtube": "youtube video"}.get(mr.platform, mr.platform)
    return {"9:16": "vertical 9:16", "16:9": "landscape 16:9", "1:1": "square 1:1"}.get(mr.aspect_ratio, "")
