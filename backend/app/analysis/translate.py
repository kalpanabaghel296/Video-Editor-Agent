"""Translate caption text. Providers (TRANSLATOR env var):
  llm    - the configured LLM (LLM_PROVIDER = ollama | anthropic), best quality, understands Hinglish
  google - Google Translate web endpoint through httpx (no API key, needs internet)
  auto   - llm when LLM_PROVIDER is set, otherwise google (default);  none = never translate
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

import httpx

from .. import config
from ..editing.languages import BY_CODE


class TranslationUnavailable(RuntimeError):
    pass


def _chunks(text: str, limit: int = 1500) -> List[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for w in text.split(" "):
        if len(cur) + len(w) + 1 > limit and cur:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + ([cur] if cur else [])


def parse_google(data) -> str:
    """translate_a/single answer: [[["translated part", "original part", ...], ...], ...]"""
    return "".join(p[0] for p in data[0] if p and isinstance(p[0], str))


def _google(texts: List[str], target: str, source: Optional[str]) -> List[str]:
    def one(client: httpx.Client, text: str) -> str:
        if not text.strip():
            return text
        parts = []
        for piece in _chunks(text):
            r = client.get("https://translate.googleapis.com/translate_a/single",
                           params={"client": "gtx", "sl": source or "auto", "tl": target, "dt": "t", "q": piece})
            r.raise_for_status()
            parts.append(parse_google(r.json()))
        return " ".join(parts)

    with httpx.Client(timeout=config.TRANSLATE_TIMEOUT) as c, ThreadPoolExecutor(4) as ex:
        return list(ex.map(lambda t: one(c, t), texts))


def _llm_call(prompt: str) -> str:
    if config.LLM_PROVIDER == "ollama":
        r = httpx.post(f"{config.OLLAMA_URL}/api/generate", timeout=180,
                       json={"model": config.OLLAMA_MODEL, "prompt": prompt, "format": "json", "stream": False})
        return r.json().get("response", "")
    if config.LLM_PROVIDER == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise TranslationUnavailable("ANTHROPIC_API_KEY not set")
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120,
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                       json={"model": config.ANTHROPIC_MODEL, "max_tokens": 4000,
                             "messages": [{"role": "user", "content": prompt}]})
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    raise TranslationUnavailable("no LLM configured")


def _llm(texts: List[str], target: str, source: Optional[str]) -> List[str]:
    lang = BY_CODE.get(target, {}).get("name", target)
    out: List[str] = []
    for i in range(0, len(texts), 25):                      # batches keep the answer short and the order checkable
        batch = texts[i:i + 25]
        prompt = (f"Translate every string of the JSON array into {lang}, written in {lang}'s own script. "
                  f"They are spoken sentences of a video (may be Hinglish / Roman script): keep the meaning and a natural spoken tone, "
                  f"no explanations. Reply with ONLY a JSON object {{\"t\": [...]}} holding exactly {len(batch)} strings in the same order.\n"
                  f"{json.dumps(batch, ensure_ascii=False)}")
        raw = _llm_call(prompt)
        m = re.search(r"\{.*\}", raw, re.S)
        got = json.loads(m.group(0)).get("t") if m else None
        if not isinstance(got, list) or len(got) != len(batch):
            raise TranslationUnavailable("LLM answer did not match the request")
        out += [str(x) for x in got]
    return out


def translate_texts(texts: List[str], target: str, source: Optional[str] = None) -> List[str]:
    """texts -> same number of texts in `target` (ISO code). Raises TranslationUnavailable."""
    mode = config.TRANSLATOR
    if mode == "none":
        raise TranslationUnavailable("TRANSLATOR=none")
    order = {"llm": ["llm"], "google": ["google"]}.get(mode) or (["llm", "google"] if config.LLM_PROVIDER != "none" else ["google"])
    fns = {"llm": _llm, "google": _google}
    errors = []
    for name in order:
        try:
            out = fns[name](texts, target, source)
            if len(out) == len(texts) and all(o.strip() for o, t in zip(out, texts) if t.strip()):
                return out
            errors.append(f"{name}: empty answer")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    raise TranslationUnavailable("; ".join(errors))