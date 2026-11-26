"""Caption language: 7 languages, native script, translation, fonts, API."""
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import TRANSCRIPT, make_video
from app import config
from app.agents import caption_lang, director, loop
from app.analysis import transcription, translate
from app.analysis.pipeline import load_analysis, run_observe
from app.editing import languages as L
from app.editing.captions import write_ass
from app.models.edl import Caption, CaptionSettings
from app.models.transcript import Segment, Transcript, Word
from app.services.jobs import JobStore

KN = "ಈ ವೀಡಿಯೊ ಬಗ್ಗೆ ಮಾತು"
HI = "यह वीडियो के बारे में है"


# ---------------------------------------------------------------- the 7 languages / fonts / parsing
def test_seven_languages_offered():
    assert [l["code"] for l in L.LANGUAGES] == ["en", "hi", "kn", "ta", "te", "bn", "es"]
    assert all(l["native"] for l in L.LANGUAGES)


def test_normalize_and_script_detection():
    assert L.normalize("hi-IN") == "hi" and L.normalize("KN") == "kn"
    assert L.normalize("auto") is None and L.normalize("") is None and L.normalize("xx") is None
    assert L.detect_script(KN) == "kannada" and L.detect_script(HI) == "devanagari"
    assert L.detect_script("hello yeh video") is None
    assert L.has_script(HI, "devanagari") and not L.has_script("yeh video", "devanagari")


@pytest.mark.parametrize("code,text,script", [("hi", HI, "devanagari"), ("kn", KN, "kannada"), ("ta", "இந்த வீடியோ", "tamil"),
                                              ("te", "ఈ వీడియో", "telugu"), ("bn", "এই ভিডিওটি", "bengali")])
def test_indic_text_gets_an_indic_font_not_arial(code, text, script):
    from app.editing.captions import pick_font, _STYLES
    f = pick_font([Caption(start=0, end=1, text=text)], _STYLES["classic"], None, code)
    assert f == L.font_for_script(script) and f != "Arial"


def test_latin_languages_keep_the_preset_font():
    from app.editing.captions import pick_font, _STYLES
    assert pick_font([Caption(start=0, end=1, text="Hola amigos")], _STYLES["hormozi"], None, "es") == "Arial Black"


def test_ass_uses_the_script_font(tmp_path):
    cap = Caption(start=0, end=2, text=KN, words=[])
    ass = write_ass([cap], tmp_path / "a.ass", 720, 405, CaptionSettings(enabled=True, language="kn")).read_text(encoding="utf-8")
    assert KN in ass and L.font_for_script("kannada") in ass


@pytest.mark.parametrize("prompt,code", [("make a video with captions in hindi", "hi"), ("kannada captions please", "kn"),
                                         ("add captions in Tamil", "ta"), ("subtitles in spanish", "es"),
                                         ("captions telugu mein", "te"), ("bengali subtitles", "bn")])
def test_prompt_can_ask_for_a_caption_language(prompt, code):
    from app.models.metadata import VideoMetadata
    meta = VideoMetadata(duration=15, width=640, height=360, fps=25, has_audio=True)
    assert director.understand(prompt, meta).caption_language == code


def test_a_hindi_video_is_not_a_hindi_caption_request():
    from app.models.metadata import VideoMetadata
    meta = VideoMetadata(duration=15, width=640, height=360, fps=25, has_audio=True)
    assert director.understand("make a hindi video with captions", meta).caption_language is None


# ---------------------------------------------------------------- translation plumbing
def test_google_answer_is_parsed():
    data = [[["नमस्ते ", "hello ", None, None, 10], ["दोस्तों", "friends", None, None, 10]], None, "en"]
    assert translate.parse_google(data) == "नमस्ते दोस्तों"


def test_translator_none_and_failures_raise(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATOR", "none")
    with pytest.raises(translate.TranslationUnavailable):
        translate.translate_texts(["hi"], "hi")
    monkeypatch.setattr(config, "TRANSLATOR", "google")
    monkeypatch.setattr(translate, "_google", lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(translate.TranslationUnavailable) as e:
        translate.translate_texts(["hi"], "hi")
    assert "offline" in str(e.value)


def test_llm_answer_must_match_the_request(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "TRANSLATOR", "llm")
    monkeypatch.setattr(translate, "_llm_call", lambda p: json.dumps({"t": ["a", "b"]}))
    assert translate.translate_texts(["x", "y"], "kn") == ["a", "b"]
    monkeypatch.setattr(translate, "_llm_call", lambda p: json.dumps({"t": ["only one"]}))
    with pytest.raises(translate.TranslationUnavailable):
        translate.translate_texts(["x", "y"], "kn")


def test_retime_keeps_translated_words_inside_the_original_speech():
    seg = Segment(start=2.0, end=4.0, text="one two three four",
                  words=[Word(text=w, start=2 + i * 0.5, end=2.5 + i * 0.5) for i, w in enumerate("one two three four".split())])
    out = caption_lang.retime(seg, "ek do teen char paanch chhe")
    assert len(out.words) == 6
    assert out.words[0].start == pytest.approx(2.0) and out.words[-1].end == pytest.approx(4.0)
    assert all(a.end <= b.start + 0.06 for a, b in zip(out.words, out.words[1:]))
    assert all(w.end > w.start for w in out.words)


# ---------------------------------------------------------------- end to end on the pipeline
def fake_google(texts, target, source):
    mark = {"kn": "ಕನ್ನಡ", "hi": "हिन्दी", "es": "Hola"}.get(target, target)
    return [f"{mark} {i} " + " ".join(t.split()[:3]) for i, t in enumerate(texts)]


def run_job(job_dir: Path, instruction: str, name: str, style=None, transcript=None):
    store = JobStore(job_dir.parent / name)
    jid = store.create("x.mp4")
    shutil.copy(job_dir / "source" / "input.mp4", store.path(jid) / "source" / "input.mp4")
    for f in (job_dir / "analysis").glob("*.json"):
        shutil.copy(f, store.path(jid) / "analysis" / f.name)
    if transcript is not None:
        (store.path(jid) / "analysis" / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    if style:
        a = store.path(jid) / "assets"
        a.mkdir(exist_ok=True)
        (a / "caption_style.json").write_text(json.dumps(style), encoding="utf-8")
    loop.run_agent(store, jid, instruction)
    job = store.load(jid)
    assert job["status"] == "done", job.get("error")
    return store, jid, job


def srt(store, jid):
    return (store.path(jid) / "output" / "final.srt").read_text(encoding="utf-8")


@pytest.fixture
def translator(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATOR", "google")
    calls = []
    monkeypatch.setattr(translate, "_google", lambda *a: (calls.append(a) or fake_google(*a)))
    return calls


def test_translated_captions_end_up_in_the_video_and_srt(job_dir, translator):
    tr = dict(TRANSCRIPT, language="en")
    store, jid, job = run_job(job_dir, "make a 10 second video with captions", "tr_kn", {"language": "kn"}, tr)
    assert "ಕನ್ನಡ" in srt(store, jid)
    edl = json.loads((store.path(jid) / "edl.json").read_text(encoding="utf-8"))
    assert edl["caption_settings"]["language"] == "kn" and edl["captions"]
    assert not job["warnings"]
    assert (store.path(jid) / "analysis" / "transcript.to.kn.json").exists()


def test_translation_is_cached(job_dir, translator):
    tr = dict(TRANSCRIPT, language="en")
    store, jid, _ = run_job(job_dir, "make a 10 second video with captions", "tr_cache", {"language": "es"}, tr)
    n = len(translator)
    assert n >= 1
    loop.run_agent(store, jid, "make a 10 second video with captions")
    assert len(translator) == n                                   # second run: no new translation requests


def test_same_language_in_native_script_uses_a_native_whisper_run(job_dir, translator, monkeypatch):
    """Roman Hinglish transcript (language hi) + Hindi captions -> Devanagari from a native run, no translation needed."""
    seen = []

    def fake_native(video, work, duration, language):
        seen.append(language)
        t = json.loads(json.dumps(TRANSCRIPT))
        for s in t["segments"]:
            s["text"] = HI
            s["words"] = [dict(w, text=HI.split()[i % 5]) for i, w in enumerate(s["words"])]
        return Transcript(**dict(t, language="hi"))

    monkeypatch.setattr(transcription, "transcribe_native", fake_native)
    tr = dict(TRANSCRIPT, language="hi")
    store, jid, job = run_job(job_dir, "make a 10 second video with captions", "tr_hi", {"language": "hi"}, tr)
    assert seen == ["hi"] and not translator
    assert any("\u0900" <= ch <= "\u097f" for ch in srt(store, jid))


def test_other_language_from_roman_hindi_translates_from_clean_devanagari(job_dir, translator, monkeypatch):
    seen_src = []
    monkeypatch.setattr(transcription, "transcribe_native",
                        lambda video, work, duration, language: Transcript(**dict(json.loads(json.dumps(TRANSCRIPT)), language="hi")))
    orig = translator

    def spy(texts, target, source):
        seen_src.append(source)
        return fake_google(texts, target, source)
    monkeypatch.setattr(translate, "_google", spy)
    tr = dict(TRANSCRIPT, language="hi")
    store, jid, _ = run_job(job_dir, "make a 10 second video with captions", "tr_hi_kn", {"language": "kn"}, tr)
    assert seen_src == ["hi"] and "ಕನ್ನಡ" in srt(store, jid)


def test_failed_translation_keeps_original_captions_and_warns(job_dir, monkeypatch):
    monkeypatch.setattr(config, "TRANSLATOR", "google")
    monkeypatch.setattr(translate, "_google", lambda *a: (_ for _ in ()).throw(RuntimeError("no internet")))
    tr = dict(TRANSCRIPT, language="en")
    store, jid, job = run_job(job_dir, "make a 10 second video with captions", "tr_fail", {"language": "kn"}, tr)
    assert any("Kannada" in w and "nahi" in w for w in job["warnings"])
    assert srt(store, jid).strip()                                 # normal captions are still there


def test_prompt_language_is_used_when_the_ui_has_no_choice(job_dir, translator):
    tr = dict(TRANSCRIPT, language="en")
    store, jid, _ = run_job(job_dir, "make a 10 second video with captions in spanish", "tr_prompt", None, tr)
    assert "Hola" in srt(store, jid)


def test_dictated_caption_script_is_never_translated(job_dir, translator):
    tr = dict(TRANSCRIPT, language="en")
    prompt = 'make a 6 second video. Caption Breakdown: 00:00 – 00:03: "Hello there" 00:03 – 00:06: "Second line"'
    store, jid, _ = run_job(job_dir, prompt, "tr_script", {"language": "kn"}, tr)
    assert not translator
    assert "Hello there" in srt(store, jid)


# ---------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "store", JobStore(tmp_path / "jobs"))
    return TestClient(main.app)


def wait_idle(c, jid, limit=180):
    for _ in range(limit):
        s = c.get(f"/api/jobs/{jid}").json()
        if not s["busy"] and s["status"] in ("done", "failed", "analyzed", "uploaded"):
            return s
        time.sleep(1)
    raise AssertionError("timeout")


def test_api_lists_languages_and_validates(client, tmp_path):
    langs = client.get("/api/caption-languages").json()["languages"]
    assert [l["code"] for l in langs] == ["en", "hi", "kn", "ta", "te", "bn", "es"]
    a = make_video(tmp_path / "a.mp4")
    jid = client.post("/api/jobs", files={"file": ("a.mp4", a.read_bytes(), "video/mp4")}, data={"defer": "true"}).json()["id"]
    assert client.put(f"/api/jobs/{jid}/caption-style", json={"language": "xx"}).status_code == 400
    assert client.put(f"/api/jobs/{jid}/caption-style", json={"language": "kn"}).json() == {"language": "kn"}
    assert client.get(f"/api/jobs/{jid}/caption-style").json() == {"language": "kn"}
    assert client.put(f"/api/jobs/{jid}/caption-style", json={"preset": "hormozi", "language": "auto"}).json() == {"preset": "hormozi"}


def test_change_language_after_the_edit(client, tmp_path, monkeypatch, job_dir):
    monkeypatch.setattr(config, "TRANSLATOR", "google")
    monkeypatch.setattr(translate, "_google", fake_google)
    from app import main
    a = make_video(tmp_path / "a.mp4")
    jid = client.post("/api/jobs", files={"file": ("a.mp4", a.read_bytes(), "video/mp4")}, data={"defer": "true"}).json()["id"]
    wait_idle(client, jid)
    ad = main.store.path(jid) / "analysis"
    ad.mkdir(exist_ok=True)
    (ad / "transcript.json").write_text(json.dumps(dict(TRANSCRIPT, language="en")), encoding="utf-8")
    client.post(f"/api/jobs/{jid}/edit", json={"instruction": "make a 10 second video with captions"})
    assert wait_idle(client, jid)["status"] == "done"
    base = main.store.path(jid) / "work" / "base.mp4"
    m0 = base.stat().st_mtime_ns
    assert client.post(f"/api/jobs/{jid}/captions/regenerate", json={"language": "kn"}).status_code == 200
    assert wait_idle(client, jid)["status"] == "done"
    assert "ಕನ್ನಡ" in (main.store.path(jid) / "output" / "final.srt").read_text(encoding="utf-8")
    assert base.stat().st_mtime_ns == m0                           # only captions were re-rendered
    assert client.get(f"/api/jobs/{jid}/caption-style").json()["language"] == "kn"
    # manual edit in Kannada, then back to "as spoken"
    caps = client.get(f"/api/jobs/{jid}/captions?kind=final&format=json").json()
    lines = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in caps]
    lines[0]["text"] = "ನನ್ನ ಸ್ವಂತ ಸಾಲು"
    client.put(f"/api/jobs/{jid}/captions/edit", json={"captions": lines})
    assert wait_idle(client, jid)["status"] == "done"
    assert client.get(f"/api/jobs/{jid}/caption-style").json()["language"] == "kn"      # edit did not lose the language
    assert client.post(f"/api/jobs/{jid}/captions/regenerate", json={"language": "auto"}).status_code == 200
    assert wait_idle(client, jid)["status"] == "done"
    assert "ಕನ್ನಡ" not in (main.store.path(jid) / "output" / "final.srt").read_text(encoding="utf-8")
    assert "language" not in client.get(f"/api/jobs/{jid}/caption-style").json()