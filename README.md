# 🎬 AI Video Editing Agent

Upload a talking-style video, type what you want in plain language, and get the edited video back.
The agent **watches** the video, **plans** the cuts, **renders** it, **checks its own output** and **repairs** it if something is wrong. You never touch a timeline.

> *"Make a 30 second vertical reel with captions and calm background music, remove pauses and mistakes."*

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688) ![FFmpeg](https://img.shields.io/badge/render-FFmpeg-green) ![Whisper](https://img.shields.io/badge/speech-faster--whisper-orange)

![Editor UI](docs/images/ui-editor.png)

---

## ✨ Features

**Understands your prompt**
- Length and time windows: `10 seconds`, `0:00–0:15`, `first 20 seconds`, `1:05 to 1:35`
- Formats: vertical 9:16, square 1:1, 16:9
- Captions, zoom cuts, sound effects, noise removal, music, filters, brightness and more
- Your own caption script: lines like `00:03 – 00:07: "text"` are used exactly as written
- **Hinglish**: `isko chhota kar do, galtiyan hata do, caption laga do`, `zoom kam karo`, `music hata do`
- **Platform presets**: Instagram Reel, YouTube Short, TikTok, LinkedIn, YouTube set the size, caption look and a safe zone

**Chat revise** (week 7)
- After a result, say what to change: `zoom kam karo`, `caption chhote karo`, `20 second me karo`, `thoda aur chhota`, `black and white kar do`
- The message is merged into the previous instruction (old conflicting words are removed) and the agent re-runs. No LLM needed.

**Reference style matching** (week 7)
- Upload a reel you like. Its **pace** (average shot length), **format**, **colour look** and optionally **length** are measured and applied to your video
- The look is moved *relative to your own footage*; anything you write in the prompt always wins
- Not detected (no ML model on purpose): caption style, music vs speech, transitions

**Edits like an editor (the agent "brain")**
- Cuts long pauses (also pauses hidden by background noise), stutters, false starts, and **re-takes**
- **Best-take selection** (week 7): when you say a line 2-3 times, every take is scored (fluency, completeness, pace, audio level) and the **best one is kept**, not just the last
- **Face-aware reframing** (week 7): wide video to 9:16 / 1:1 follows the speaker with a steady "virtual tripod" crop; `blurred background` is available too
- Hits the length you ask for (a 10 s request gives about 10 s), ending on a clean word
- *Enhance-only* prompts ("remove noise", "add music", "make it black and white") never delete a frame
- Every cut is explained with a reason and a timestamp

**Audio**
- Background noise removal (audio only, the timeline stays the same)
- Built-in royalty-free music (calm / neutral / upbeat), or upload your own track; the music dips automatically while someone speaks
- Whoosh, pop, click and ding sound effects on cuts and captions
- Quiet recordings are lifted towards a standard loudness

**Captions**
- Word-level Whisper transcript, animated captions with a highlighted word
- **5 caption looks** (week 7): Classic, Hormozi (big, 2-3 words, spoken word highlighted), Minimal, Boxed, Karaoke; 3 sizes; platform safe zone; Hindi script uses Nirmala UI
- Fix a wrong line in the UI, then **Save & re-run**; SRT and VTT download

**Look & Effects**
- 9 filters: Cinematic, Warm, Cool, B&W, Noir, Vintage, Vivid, Faded
- Sliders: brightness, contrast, saturation, warmth, sharpen, vignette, film grain
- Fade in/out, cinema bars, mirror; live preview before you render

**Reliable**
- The plan is saved as readable JSON (an *Edit Decision List*); the original video is never changed
- The result is validated and inspected; a broken part is patched and re-rendered (max 3 tries, 4 fallback levels)
- Runs fully on your machine: no cloud, no paid API key

![Look & Effects](docs/images/ui-look.png)

---

## 🧠 How it works

```mermaid
flowchart LR
    A[Upload] --> B[OBSERVE<br/>metadata · transcript<br/>scenes · silence · fillers]
    B --> C[UNDERSTAND<br/>Director: prompt → requirements]
    C --> D[PLAN<br/>Planner → EDL JSON]
    D --> E[ACT<br/>FFmpeg render]
    E --> F[INSPECT<br/>validator + inspector]
    F -->|problem| G[REVISE<br/>patch + re-render]
    G --> E
    F -->|ok| H[EXPORT<br/>final.mp4 + captions]
```

Every stage writes its result into the job folder (`jobs/<id>/`), so any decision can be opened and checked later. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

---

## 🚀 Quick start

**Requirements:** Python 3.10+ and **FFmpeg + ffprobe**.

```bash
# Linux / macOS
./run.sh

# Windows
run.bat          # or:  .\run.ps1
```

Open **http://localhost:8000**. The frontend is served by the same FastAPI app.

**Windows notes**
- Install FFmpeg once: `winget install Gyan.FFmpeg`. The app also finds it when the server was started *before* the install (it checks the registry PATH and the winget folders), or set `FFMPEG_PATH` to the folder that contains `ffmpeg.exe`.
- The UI shows a red banner (and `/api/health` says `ffmpeg_ok: false`) while FFmpeg is missing. `[WinError 2] The system cannot find the file specified` always means this.
- If `pip.exe` / `uvicorn.exe` are blocked by *Application Control*, run them as modules (the run scripts already do): `python -m pip install -r backend\requirements.txt` and `python -m uvicorn app.main:app --port 8000` (from `backend\`).

The Whisper model downloads on first use. Without a transcriber the agent still works (it plans from speech/silence regions only, without caption text).

### Use it
1. Drop a video on the page.
2. Write an instruction in the **Prompt** tab (or use the quick-add chips).
3. Optionally choose a **Look**, add **music**, or fix the **captions**.
4. Press **Edit video**. Use **Re-run** to change the instruction or look without re-analysing.

### Prompt examples
```text
Make a 45 second reel with captions and music
YouTube version, 1 minute, remove silence and fillers
Trim the video to 10 seconds
Remove the background noise and disturbance
Make it black and white with a vignette and fade in and out
Vertical 9:16 with zoom cuts and whoosh sound effects, calm background music
```

---

## ⚙️ Configuration

Set as environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` (bigger = more accurate, slower) |
| `WHISPER_LANGUAGE` | auto | Language hint, e.g. `en`, `hi` (use `hi` for Hindi / Hinglish speakers) |
| `WHISPER_PROMPT` | Hinglish sentence | Text that guides spelling style |
| `WHISPER_BEAM` | `5` | Beam size |
| `TRANSCRIBER` | `auto` | `auto`, `faster-whisper`, `whisper`, `none` |
| `SILENCE_DB` / `SILENCE_MIN` | `-35` / `0.5` | Silence threshold (dB) and minimum length (s) |
| `SCENE_THRESHOLD` | `27.0` | Scene-change sensitivity |
| `MAX_RETRIES` | `3` | Repair attempts before a job is marked failed |
| `LLM_PROVIDER` | `none` | `none` (rules only), `ollama`, `anthropic` |
| `OLLAMA_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `llama3.1` | Local LLM settings |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | – | Needed only if `LLM_PROVIDER=anthropic` |
| `FFMPEG_PATH` | – | Folder with `ffmpeg` + `ffprobe` (optional; `PATH` and the usual Windows folders are searched first) |
| `MAX_UPLOAD_MB` / `MAX_REFERENCE_MB` | `500` / `200` | Upload size limits for the video / a reference video |
| `REFERENCE_MAX_SECONDS` | `180` | Only this much of a reference video is analysed |
| `JOBS_DIR` | `./jobs` | Where jobs are stored |
| `MUSIC_DIR` | `./assets/music` | Drop your own mp3 here |

---

## 🔌 API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/jobs` | Upload a video (+ optional `instruction`, `music`, `look`, `caption_style`); size limit + real-video check |
| `POST` | `/api/jobs/{id}/chat` | Chat revise: `{"message": "zoom kam karo"}` → merged into the instruction and re-run |
| `POST` `GET` `PUT` `DELETE` | `/api/jobs/{id}/reference`, `/reference/options` | Reference video for style matching |
| `PUT` `GET` | `/api/jobs/{id}/caption-style` | Caption look chosen in the UI (`preset`, `size`) |
| `GET` | `/api/jobs/{id}/takes` | Repeated takes found and which one is kept |
| `GET` | `/api/presets` | Platform presets and caption looks |
| `GET` | `/api/jobs`, `/api/jobs/{id}` | List jobs / job status |
| `POST` | `/api/jobs/{id}/edit` | Run an instruction (`retranscribe` optional) |
| `GET` | `/api/jobs/{id}/analysis` | Metadata, transcript, scenes, silences, fillers |
| `GET` | `/api/jobs/{id}/edl`, `/edl/preview` | The plan (JSON / readable) |
| `PUT` `GET` | `/api/jobs/{id}/look` | Save / read look & effects |
| `POST` `DELETE` | `/api/jobs/{id}/music` | Upload / remove background music |
| `PUT` | `/api/jobs/{id}/transcript` | Save corrected captions |
| `GET` | `/api/jobs/{id}/video?kind=source\|draft\|final` | Stream video (Range supported) |
| `GET` | `/api/jobs/{id}/audio-check` | Does the file have audible sound, and how loud |
| `GET` | `/api/jobs/{id}/captions`, `/captions/download` | VTT / SRT |
| `GET` | `/api/jobs/{id}/download`, `/log`, `/api/health` | Download, log, health |

---

## 📁 Project structure

```text
backend/app/
├── main.py                  FastAPI app and routes
├── config.py                settings (environment variables)
├── agents/
│   ├── director.py          prompt → requirements (rules, optional LLM)
│   ├── planner.py           segments → EDL (selection, exact length, brain cuts)
│   ├── presets.py           platform presets (week 7)
│   ├── revise.py            chat revise (week 7)
│   ├── validator.py         checks / patches the plan
│   ├── inspector.py         checks the rendered file
│   └── loop.py              observe → … → export, retry loop
├── analysis/
│   ├── metadata.py  transcription.py  scenes.py  silence.py  fillers.py
│   ├── fumbles.py           pauses, stutters, false starts, retakes
│   ├── takes.py             best-take selection (week 7)
│   ├── reference.py         reference-video style profile (week 7)
│   └── pipeline.py          runs and caches the analysis
├── editing/
│   ├── renderer.py          EDL → FFmpeg (3 passes, fallback levels)
│   ├── captions.py          captions, 5 ASS looks, SRT/VTT
│   ├── reframe.py           face-aware crop paths (week 7)
│   ├── denoise.py           spectral noise removal
│   ├── music.py             built-in music generator
│   └── look.py              filters, colour, effects
├── models/                  Pydantic models (EDL, transcript, analysis…)
├── services/jobs.py         job folders and state
└── utils/ffmpeg.py  errors.py     FFmpeg lookup + friendly error messages
backend/tests/               pytest suite
frontend/index.html          single-page studio UI
docs/                        architecture, sample EDL, changelog
```

---

## 🧪 Tests

```bash
cd backend
pip install -r requirements.txt
pytest -q
```

Uses synthetic test videos; no Whisper model is needed. Coverage includes the pipeline, exact-length trimming, noise removal quality, music level, the agent brain, every look control (checked by measuring pixels), and the API.

Week 7 adds `tests/test_week7.py`: Hinglish prompts, presets, chat revise, best-take scoring, a moving-subject video proving the tracked crop keeps the subject in frame (and the centre crop does not), all 5 caption looks through libass, reference profiling, and the new API endpoints.

---

## 📌 Project status

| Area | Status |
|---|---|
| Upload, job folders, metadata | ✅ |
| Transcript, scenes, silence, fillers | ✅ |
| Director, Planner, EDL schema | ✅ |
| Renderer: cuts, aspect ratio, zoom, fades, captions, titles | ✅ |
| Validator, Inspector, revise loop, fallback levels | ✅ |
| Agent brain (pauses, stutters, retakes), exact length | ✅ |
| Noise removal, music, sound effects, loudness | ✅ |
| Look & Effects, studio UI, caption editor, sound check | ✅ |
| Measuring Hinglish caption accuracy with real Whisper on more videos | ⏳ |
| Reference-video style matching (pace, format, look) | ✅ week 7 |
| Face-aware reframing + blurred-background fill | ✅ week 7 |
| Best-take selection from repeated takes | ✅ week 7 |
| Chat revise, platform presets, caption looks, Hinglish prompts | ✅ week 7 |
| Upload size limit + real-video check, FFmpeg detection + banner | ✅ week 7 |
| Face tracking measured on real faces (Haar cascade) | ⏳ test on your own videos |
| LLM director tested end to end | ⏳ |

See [docs/ROADMAP.md](docs/ROADMAP.md) for what is next.

---

## 👩‍💻 Team

B.Tech Major Project, Institute of Engineering and Technology (IET), JK Lakshmipat University.

**Kalpana Baghel · Riddhi Khandelwal · Tanishka Nagal**