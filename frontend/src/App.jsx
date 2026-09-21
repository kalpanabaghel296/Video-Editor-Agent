import React, { useState, useEffect, useRef } from "react";
import {
  uploadVideo,
  getJobStatus,
  getVideoStreamUrl,
  triggerTranscription,
  triggerSilenceDetection,
  triggerSceneDetection,
  triggerPlanning,
  triggerRender,
  getMusicTracks,
  getPresets,
  triggerAutoEdit,
  submitRevision,
  getRenderedVideoUrl,
  getSubtitlesDownloadUrl,
  getValidationReport,
} from "./services/api";
import "./App.css";

function App() {
  const [file, setFile] = useState(null);
  const [instruction, setInstruction] = useState(
    "Create a 30 second energetic short-form video. Remove unnecessary silence and keep the most important spoken parts."
  );
  const [uploadLoading, setUploadLoading] = useState(false);
  const [statusLoading, setStatusLoading] = useState(false);
  const [error, setError] = useState(null);

  // Job data
  const [jobData, setJobData] = useState(null);
  const [currentStatus, setCurrentStatus] = useState(null);

  // Analysis & Planning state
  const [analysisTab, setAnalysisTab] = useState("transcript"); // "transcript" | "silence" | "scenes" | "plan"
  const [transcription, setTranscription] = useState(null);
  const [transcribeLoading, setTranscribeLoading] = useState(false);
  const [whisperModel, setWhisperModel] = useState("base");
  const [whisperLanguage, setWhisperLanguage] = useState("en");

  const [silenceData, setSilenceData] = useState(null);
  const [silenceLoading, setSilenceLoading] = useState(false);
  const [silenceThreshold, setSilenceThreshold] = useState("-26.0");
  const [silenceDuration, setSilenceDuration] = useState("0.3");

  const [sceneData, setSceneData] = useState(null);
  const [sceneLoading, setSceneLoading] = useState(false);

  const [editPlan, setEditPlan] = useState(null);
  const [planLoading, setPlanLoading] = useState(false);

  // Week 3 & 4 Rendering & Social Polish State
  const [renderLoading, setRenderLoading] = useState(false);
  const [renderResult, setRenderResult] = useState(null);
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [bgMusic, setBgMusic] = useState("chill_ambient");
  const [musicVolume, setMusicVolume] = useState(0.15);
  const [enablePunchIn, setEnablePunchIn] = useState(true);
  const [musicTracks, setMusicTracks] = useState([
    { id: "chill_ambient", name: "Chill Ambient", filename: "chill_ambient.mp3" },
    { id: "upbeat_energetic", name: "Upbeat Energetic", filename: "upbeat_energetic.mp3" },
  ]);

  // Week 5 Autonomous Pipeline & Iterative Revision State
  const [presets, setPresets] = useState([]);
  const [selectedPreset, setSelectedPreset] = useState("reels");
  const [autoEditLoading, setAutoEditLoading] = useState(false);
  const [revisionText, setRevisionText] = useState("");
  const [revisionLoading, setRevisionLoading] = useState(false);

  const videoRef = useRef(null);

  useEffect(() => {
    getMusicTracks()
      .then((tracks) => {
        if (tracks && tracks.length > 0) {
          setMusicTracks(tracks);
        }
      })
      .catch(() => {});

    getPresets()
      .then((p) => {
        if (p && p.length > 0) {
          setPresets(p);
        }
      })
      .catch(() => {});
  }, []);

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setError(null);
    }
  };

  const handleUploadAndAnalyze = async (e) => {
    e.preventDefault();
    if (!file) {
      setError("Please select a video file first.");
      return;
    }

    setUploadLoading(true);
    setError(null);
    setJobData(null);
    setTranscription(null);
    setSilenceData(null);
    setSceneData(null);
    setEditPlan(null);
    setRenderResult(null);

    try {
      const response = await uploadVideo(file, instruction);
      setJobData(response);
      setCurrentStatus(response.status);
    } catch (err) {
      setError(err.message || "Upload failed. Please try again.");
    } finally {
      setUploadLoading(false);
    }
  };

  const handleRefreshStatus = async () => {
    if (!jobData?.job_id) return;
    setStatusLoading(true);
    try {
      const statusRes = await getJobStatus(jobData.job_id);
      setCurrentStatus(statusRes.status);
    } catch (err) {
      setError(`Failed to refresh status: ${err.message}`);
    } finally {
      setStatusLoading(false);
    }
  };

  // Seek video player to specific timestamp
  const seekVideo = (timeSec) => {
    if (videoRef.current) {
      videoRef.current.currentTime = timeSec;
      videoRef.current.play().catch(() => {});
    }
  };

  // Week 2 Action Handlers
  const handleTranscribe = async () => {
    if (!jobData?.job_id) return;
    setTranscribeLoading(true);
    setError(null);
    setAnalysisTab("transcript");
    try {
      const res = await triggerTranscription(jobData.job_id, whisperModel, whisperLanguage);
      setTranscription(res);
    } catch (err) {
      setError(`Whisper transcription failed: ${err.message}`);
    } finally {
      setTranscribeLoading(false);
    }
  };

  const handleSilenceDetect = async () => {
    if (!jobData?.job_id) return;
    setSilenceLoading(true);
    setError(null);
    setAnalysisTab("silence");
    try {
      const res = await triggerSilenceDetection(
        jobData.job_id,
        parseFloat(silenceThreshold),
        parseFloat(silenceDuration)
      );
      setSilenceData(res);
    } catch (err) {
      setError(`Silence detection failed: ${err.message}`);
    } finally {
      setSilenceLoading(false);
    }
  };

  const handleSceneDetect = async () => {
    if (!jobData?.job_id) return;
    setSceneLoading(true);
    setError(null);
    setAnalysisTab("scenes");
    try {
      const res = await triggerSceneDetection(jobData.job_id);
      setSceneData(res);
    } catch (err) {
      setError(`Scene detection failed: ${err.message}`);
    } finally {
      setSceneLoading(false);
    }
  };

  const handleGeneratePlan = async () => {
    if (!jobData?.job_id) return;
    setPlanLoading(true);
    setError(null);
    setAnalysisTab("plan");
    try {
      const res = await triggerPlanning(jobData.job_id, instruction);
      setEditPlan(res);
      setCurrentStatus("planning");
    } catch (err) {
      setError(`AI Edit Planning failed: ${err.message}`);
    } finally {
      setPlanLoading(false);
    }
  };

  // Week 5 Preset Switcher
  const handleSelectPreset = (presetId) => {
    setSelectedPreset(presetId);
    if (presetId === "custom") return;
    const p = presets.find((item) => item.preset_id === presetId);
    if (p) {
      setAspectRatio(p.aspect_ratio);
      setBurnSubtitles(p.burn_subtitles);
      setEnablePunchIn(p.enable_punch_in);
      setBgMusic(p.bg_music || "none");
      setMusicVolume(p.music_volume ?? 0.15);
    }
  };

  // Week 5 Action Handler: One-Click Autonomous End-to-End Pipeline
  const handleAutoEdit = async () => {
    if (!jobData?.job_id) return;
    setAutoEditLoading(true);
    setError(null);
    try {
      const res = await triggerAutoEdit(jobData.job_id, {
        preset: selectedPreset !== "custom" ? selectedPreset : null,
        instruction,
        aspect_ratio: aspectRatio,
        burn_subtitles: burnSubtitles,
        enable_punch_in: enablePunchIn,
        bg_music: bgMusic === "none" ? null : bgMusic,
        music_volume: musicVolume,
      });
      setRenderResult(res);
      setCurrentStatus("completed");
    } catch (err) {
      setError(`Autonomous editing failed: ${err.message}`);
    } finally {
      setAutoEditLoading(false);
    }
  };

  // Week 5 Action Handler: Conversational Re-Editing / Revision Loop
  const handleRevise = async (e) => {
    if (e) e.preventDefault();
    if (!jobData?.job_id || !revisionText.trim()) return;
    setRevisionLoading(true);
    setError(null);
    try {
      const res = await submitRevision(jobData.job_id, {
        revision_instruction: revisionText.trim(),
        aspect_ratio: aspectRatio,
        burn_subtitles: burnSubtitles,
        enable_punch_in: enablePunchIn,
        bg_music: bgMusic === "none" ? null : bgMusic,
        music_volume: musicVolume,
      });
      setRenderResult(res);
      setRevisionText("");
    } catch (err) {
      setError(`Revision re-rendering failed: ${err.message}`);
    } finally {
      setRevisionLoading(false);
    }
  };

  // Week 3 & 4 Action Handler: Render Final Video with Social Polish & Punch-In
  const handleRender = async () => {
    if (!jobData?.job_id) return;
    setRenderLoading(true);
    setError(null);
    try {
      // Auto-generate plan if not already present
      if (!editPlan) {
        const p = await triggerPlanning(jobData.job_id, instruction);
        setEditPlan(p);
      }
      const res = await triggerRender(jobData.job_id, {
        burnSubtitles,
        aspectRatio,
        bgMusic: bgMusic === "none" ? null : bgMusic,
        musicVolume,
        enablePunchIn,
      });
      setRenderResult(res);
      setCurrentStatus(res.status);
    } catch (err) {
      setError(`Rendering failed: ${err.message}`);
    } finally {
      setRenderLoading(false);
    }
  };

  const formatDuration = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "N/A";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    const ms = (seconds % 1).toFixed(2).slice(2);
    return `${mins.toString().padStart(2, "0")}:${secs
      .toString()
      .padStart(2, "0")}.${ms} (${Number(seconds).toFixed(2)}s)`;
  };

  const metadata = jobData?.metadata;
  const primaryVideo = metadata?.video_streams?.[0];
  const primaryAudio = metadata?.audio_streams?.[0];

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>AI Video Editing Agent</h1>
        <p className="subtitle">
          Week 5: Autonomous End-to-End Orchestrator, Iterative Feedback & Visual Punch-in Polish
        </p>
      </header>

      <main className="main-content">
        {/* Upload Card */}
        <form className="upload-card" onSubmit={handleUploadAndAnalyze}>
          <h2>Upload & Ingest Video</h2>

          <div className="form-group">
            <label htmlFor="video-input">Select Video File (.mp4, .mov, etc.)</label>
            <input
              id="video-input"
              type="file"
              accept="video/*"
              onChange={handleFileChange}
              disabled={uploadLoading}
              className="file-input"
            />
            {file && (
              <p className="file-info">
                Selected: <strong>{file.name}</strong> (
                {(file.size / (1024 * 1024)).toFixed(2)} MB)
              </p>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="instruction-input">Director Editing Instruction</label>
            <textarea
              id="instruction-input"
              rows={3}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="Describe the desired video edit..."
              disabled={uploadLoading}
              className="textarea-input"
            />
          </div>

          <button
            type="submit"
            disabled={!file || uploadLoading}
            className="submit-btn"
          >
            {uploadLoading ? "Uploading & Analyzing..." : "Upload & Analyze Video"}
          </button>
        </form>

        {uploadLoading && (
          <div className="loading-card">
            <div className="spinner"></div>
            <p>Ingesting video, scaffolding job, and running FFprobe analysis...</p>
          </div>
        )}

        {error && (
          <div className="error-card">
            <strong>Notice:</strong> {error}
          </div>
        )}

        {/* Results Workspace */}
        {jobData && (
          <div className="workspace-container">
            {/* Week 5: Autonomous One-Click Studio */}
            <div className="card autonomous-studio-card">
              <div className="auto-studio-header">
                <div>
                  <h2>⚡ Autonomous AI Director Studio</h2>
                  <p className="actions-subtitle">
                    One-click autonomous pipeline: transcribes speech, eliminates silence, plans cuts, reframes canvas, adds punch-in zoom, ducks audio, and validates.
                  </p>
                </div>
                <button
                  onClick={handleAutoEdit}
                  disabled={autoEditLoading || uploadLoading}
                  className={`action-btn action-btn-autonomous ${autoEditLoading ? "loading" : ""}`}
                >
                  {autoEditLoading ? "⚡ Autonomous Loop Running..." : "⚡ Run Autonomous 1-Click Edit"}
                </button>
              </div>

              {/* Preset Selector */}
              <div className="presets-selector">
                <span className="presets-label">Target Preset:</span>
                <div className="presets-pills">
                  <button
                    type="button"
                    className={`preset-pill ${selectedPreset === "reels" ? "active" : ""}`}
                    onClick={() => handleSelectPreset("reels")}
                  >
                    📱 Reels & TikTok (9:16)
                  </button>
                  <button
                    type="button"
                    className={`preset-pill ${selectedPreset === "shorts" ? "active" : ""}`}
                    onClick={() => handleSelectPreset("shorts")}
                  >
                    🔴 YouTube Shorts (9:16)
                  </button>
                  <button
                    type="button"
                    className={`preset-pill ${selectedPreset === "youtube" ? "active" : ""}`}
                    onClick={() => handleSelectPreset("youtube")}
                  >
                    💻 YouTube (16:9)
                  </button>
                  <button
                    type="button"
                    className={`preset-pill ${selectedPreset === "square" ? "active" : ""}`}
                    onClick={() => handleSelectPreset("square")}
                  >
                    🟦 Square Feed (1:1)
                  </button>
                  <button
                    type="button"
                    className={`preset-pill ${selectedPreset === "custom" ? "active" : ""}`}
                    onClick={() => handleSelectPreset("custom")}
                  >
                    ⚙ Custom
                  </button>
                </div>
              </div>

              {autoEditLoading && (
                <div className="loading-card auto-loading-box">
                  <div className="spinner"></div>
                  <div>
                    <p className="loading-title">Autonomous Agent Loop Executing...</p>
                    <p className="loading-sub">
                      Analyzing Whisper speech & silence ➔ Formulating EDL cuts ➔ Applying punch-in zoom & social reframing ➔ Sidechain ducking audio ➔ Validating output integrity
                    </p>
                  </div>
                </div>
              )}
            </div>

            {/* Video Player Card */}
            <div className="card video-player-card">
              <h2>Source Video Preview</h2>
              <div className="video-wrapper">
                <video
                  ref={videoRef}
                  src={getVideoStreamUrl(jobData.job_id)}
                  controls
                  className="preview-video"
                />
              </div>
            </div>

            {/* AI Analysis & Action Bar */}
            <div className="card actions-card">
              <h2>AI Media Understanding & Planning</h2>
              <p className="actions-subtitle">
                Run analysis modules and trigger the AI Director to plan your video edit.
              </p>

              {/* Analysis Configuration Panels */}
              <div className="analysis-configs">
                <div className="config-box">
                  <span className="config-title">Speech-to-Text (Whisper)</span>
                  <div className="config-row">
                    <div className="config-item">
                      <label>Model</label>
                      <select
                        value={whisperModel}
                        onChange={(e) => setWhisperModel(e.target.value)}
                        className="config-select"
                        disabled={transcribeLoading}
                      >
                        <option value="base">Base (Accurate - Recommended)</option>
                        <option value="tiny">Tiny (Fastest)</option>
                      </select>
                    </div>
                    <div className="config-item">
                      <label>Language</label>
                      <select
                        value={whisperLanguage}
                        onChange={(e) => setWhisperLanguage(e.target.value)}
                        className="config-select"
                        disabled={transcribeLoading}
                      >
                        <option value="en">English (en)</option>
                        <option value="auto">Auto Detect</option>
                        <option value="hi">Hindi (hi)</option>
                      </select>
                    </div>
                  </div>
                </div>

                <div className="config-box">
                  <span className="config-title">Silence Detection (FFmpeg)</span>
                  <div className="config-row">
                    <div className="config-item">
                      <label>Threshold</label>
                      <select
                        value={silenceThreshold}
                        onChange={(e) => setSilenceThreshold(e.target.value)}
                        className="config-select"
                        disabled={silenceLoading}
                      >
                        <option value="-26.0">Phone Mic / Ambient (-26 dB)</option>
                        <option value="-22.0">Aggressive / Noisy (-22 dB)</option>
                        <option value="-35.0">Studio Clean Mic (-35 dB)</option>
                      </select>
                    </div>
                    <div className="config-item">
                      <label>Min Pause</label>
                      <select
                        value={silenceDuration}
                        onChange={(e) => setSilenceDuration(e.target.value)}
                        className="config-select"
                        disabled={silenceLoading}
                      >
                        <option value="0.3">0.3s (Short Pauses)</option>
                        <option value="0.5">0.5s (Standard)</option>
                        <option value="1.0">1.0s (Long Silence)</option>
                      </select>
                    </div>
                  </div>
                </div>
              </div>

              <div className="actions-grid">
                <button
                  onClick={handleTranscribe}
                  disabled={transcribeLoading || !metadata?.has_audio}
                  className={`action-btn ${transcribeLoading ? "loading" : ""}`}
                  title={!metadata?.has_audio ? "No audio detected in this video" : ""}
                >
                  {transcribeLoading ? "Transcribing..." : "Transcribe (Whisper)"}
                </button>

                <button
                  onClick={handleSilenceDetect}
                  disabled={silenceLoading || !metadata?.has_audio}
                  className={`action-btn ${silenceLoading ? "loading" : ""}`}
                  title={!metadata?.has_audio ? "No audio detected in this video" : ""}
                >
                  {silenceLoading ? "Detecting Silence..." : "Detect Silence"}
                </button>

                <button
                  onClick={handleSceneDetect}
                  disabled={sceneLoading}
                  className={`action-btn ${sceneLoading ? "loading" : ""}`}
                >
                  {sceneLoading ? "Detecting Scenes..." : "Detect Scenes"}
                </button>

                <button
                  onClick={handleGeneratePlan}
                  disabled={planLoading}
                  className={`action-btn action-btn-accent ${planLoading ? "loading" : ""}`}
                >
                  {planLoading ? "AI Director Thinking..." : "Generate AI Edit Plan (EDL)"}
                </button>
              </div>
            </div>

            {/* Tabbed Analysis & Planning Viewer */}
            <div className="card tabs-card">
              <div className="tab-headers">
                <button
                  className={`tab-btn ${analysisTab === "plan" ? "active" : ""}`}
                  onClick={() => setAnalysisTab("plan")}
                >
                  AI Edit Plan {editPlan && `(${editPlan.cuts.length} cuts)`}
                </button>
                <button
                  className={`tab-btn ${analysisTab === "transcript" ? "active" : ""}`}
                  onClick={() => setAnalysisTab("transcript")}
                >
                  Transcript {transcription && `(${transcription.segments.length})`}
                </button>
                <button
                  className={`tab-btn ${analysisTab === "silence" ? "active" : ""}`}
                  onClick={() => setAnalysisTab("silence")}
                >
                  Silence {silenceData && `(${silenceData.total_silence_segments})`}
                </button>
                <button
                  className={`tab-btn ${analysisTab === "scenes" ? "active" : ""}`}
                  onClick={() => setAnalysisTab("scenes")}
                >
                  Scenes {sceneData && `(${sceneData.total_scenes})`}
                </button>
              </div>

              <div className="tab-body">
                {/* AI Edit Plan Tab */}
                {analysisTab === "plan" && (
                  <div className="plan-view">
                    {editPlan ? (
                      <div>
                        <div className="plan-summary-box">
                          <p className="summary-text">{editPlan.summary}</p>
                          <div className="plan-metrics">
                            <span>
                              Original: <strong>{editPlan.original_duration}s</strong>
                            </span>
                            <span>
                              Planned: <strong>{editPlan.total_planned_duration}s</strong>
                            </span>
                            <span>
                              Silence Removed:{" "}
                              <strong>{editPlan.silence_removed_seconds}s</strong>
                            </span>
                          </div>
                        </div>

                        <h3 className="section-subtitle">Planned Cuts Timeline</h3>
                        <div className="cuts-timeline">
                          {editPlan.cuts.map((cut) => (
                            <div
                              key={cut.clip_id}
                              className="cut-item"
                              onClick={() => seekVideo(cut.start_time)}
                              title="Click to jump to clip start"
                            >
                              <div className="cut-header">
                                <span className="cut-index">Clip #{cut.clip_id}</span>
                                <span className="cut-time">
                                  {cut.start_time.toFixed(2)}s → {cut.end_time.toFixed(2)}s (
                                  {cut.duration.toFixed(2)}s)
                                </span>
                              </div>
                              <p className="cut-reason">{cut.reason}</p>
                              {cut.source_text && (
                                <p className="cut-text">"{cut.source_text}"</p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="empty-tab-state">
                        <p>No Edit Plan generated yet.</p>
                        <button
                          onClick={handleGeneratePlan}
                          className="action-btn action-btn-accent inline-btn"
                          disabled={planLoading}
                        >
                          {planLoading ? "Generating..." : "Generate Plan Now"}
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* Transcript Tab */}
                {analysisTab === "transcript" && (
                  <div className="transcript-view">
                    {transcription ? (
                      <div>
                        <div className="transcript-info">
                          <span>
                            Language: <strong>{transcription.language || "auto"}</strong>
                          </span>
                          <span>
                            Model: <strong>{transcription.model_used}</strong>
                          </span>
                          <span>
                            Segments: <strong>{transcription.segments.length}</strong>
                          </span>
                        </div>

                        {transcription.segments.length > 0 ? (
                          <div className="segments-list">
                            {transcription.segments.map((seg) => (
                              <div
                                key={seg.segment_id}
                                className="segment-item"
                                onClick={() => seekVideo(seg.start)}
                                title="Click to seek to this moment"
                              >
                                <span className="segment-time">
                                  [{seg.start.toFixed(2)}s - {seg.end.toFixed(2)}s]
                                </span>
                                <span className="segment-text">{seg.text}</span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="muted-text">
                            No spoken speech detected in this video audio.
                          </p>
                        )}
                      </div>
                    ) : (
                      <div className="empty-tab-state">
                        <p>Audio has not been transcribed yet.</p>
                        <button
                          onClick={handleTranscribe}
                          className="action-btn inline-btn"
                          disabled={transcribeLoading || !metadata?.has_audio}
                        >
                          {transcribeLoading ? "Transcribing..." : "Transcribe Now"}
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* Silence Tab */}
                {analysisTab === "silence" && (
                  <div className="silence-view">
                    {silenceData ? (
                      <div>
                        <div className="silence-summary">
                          <span>
                            Total Silence:{" "}
                            <strong>{silenceData.total_silence_duration}s</strong>
                          </span>
                          <span>
                            Segments:{" "}
                            <strong>{silenceData.total_silence_segments}</strong>
                          </span>
                        </div>
                        {silenceData.silences.length > 0 ? (
                          <div className="silence-list">
                            {silenceData.silences.map((s) => (
                              <div
                                key={s.silence_id}
                                className="silence-item"
                                onClick={() => seekVideo(s.start)}
                              >
                                <span>
                                  Silence #{s.silence_id}: {s.start}s → {s.end}s
                                </span>
                                <span className="badge-muted">
                                  {s.duration.toFixed(2)}s
                                </span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="muted-text">
                            No silent periods detected matching threshold.
                          </p>
                        )}
                      </div>
                    ) : (
                      <div className="empty-tab-state">
                        <p>Silence detection has not been run yet.</p>
                        <button
                          onClick={handleSilenceDetect}
                          className="action-btn inline-btn"
                          disabled={silenceLoading || !metadata?.has_audio}
                        >
                          {silenceLoading ? "Detecting..." : "Detect Silence Now"}
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* Scenes Tab */}
                {analysisTab === "scenes" && (
                  <div className="scenes-view">
                    {sceneData ? (
                      <div>
                        <p>
                          Total Scenes Detected:{" "}
                          <strong>{sceneData.total_scenes}</strong>
                        </p>
                        <div className="scenes-list">
                          {sceneData.scenes.map((sc) => {
                            const scId = sc.scene_number ?? sc.scene_id ?? 1;
                            const st = sc.start_time ?? sc.start_seconds ?? 0;
                            const en = sc.end_time ?? sc.end_seconds ?? 0;
                            return (
                              <div
                                key={scId}
                                className="scene-item"
                                onClick={() => seekVideo(st)}
                              >
                                <span>Scene #{scId}</span>
                                <span>
                                  {st.toFixed(2)}s → {en.toFixed(2)}s
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : (
                      <div className="empty-tab-state">
                        <p>Scene detection has not been run yet.</p>
                        <button
                          onClick={handleSceneDetect}
                          className="action-btn inline-btn"
                          disabled={sceneLoading}
                        >
                          {sceneLoading ? "Detecting..." : "Detect Scenes Now"}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Week 3 & 4: Render & Export Card */}
            <div className="card render-card">
              <div className="render-header">
                <div>
                  <h2>Render & Export Final Video</h2>
                  <p className="actions-subtitle">
                    Frame-accurate cutting, social reframing, AI subtitles, and background audio ducking via single-pass FFmpeg.
                  </p>
                </div>
                <div className="render-controls">
                  <button
                    onClick={handleRender}
                    disabled={renderLoading}
                    className={`action-btn action-btn-render ${renderLoading ? "loading" : ""}`}
                  >
                    {renderLoading ? "Rendering Final Cut..." : "Render Final Video (FFmpeg)"}
                  </button>
                </div>
              </div>

              {/* Week 4 Social Reframing & Audio Controls */}
              <div className="render-options-grid">
                <div className="render-option-box">
                  <div className="render-option-header">
                    <span className="option-title">Target Social Aspect Ratio</span>
                    <span className="option-badge">Auto Canvas</span>
                  </div>
                  <select
                    value={aspectRatio}
                    onChange={(e) => setAspectRatio(e.target.value)}
                    disabled={renderLoading}
                    className="config-select render-select"
                  >
                    <option value="9:16">9:16 Vertical (TikTok / Reels / Shorts - Smart Blur)</option>
                    <option value="1:1">1:1 Square (Instagram / LinkedIn Feed - Smart Blur)</option>
                    <option value="original">Original Aspect Ratio (Preserve Source)</option>
                  </select>
                  <p className="option-desc">
                    Blurred-background canvas prevents black bars and centers video for mobile viewers.
                  </p>
                </div>

                <div className="render-option-box">
                  <div className="render-option-header">
                    <span className="option-title">Background Music & Ducking</span>
                    <span className="option-badge">Royalty-Free</span>
                  </div>
                  <select
                    value={bgMusic}
                    onChange={(e) => setBgMusic(e.target.value)}
                    disabled={renderLoading}
                    className="config-select render-select"
                  >
                    <option value="none">None (Original Speech Only)</option>
                    {musicTracks.map((trk) => (
                      <option key={trk.id} value={trk.id}>
                        {trk.name}
                      </option>
                    ))}
                  </select>

                  {bgMusic !== "none" ? (
                    <div className="music-volume-control">
                      <div className="volume-label-row">
                        <span>Music Volume:</span>
                        <strong>{Math.round(musicVolume * 100)}%</strong>
                      </div>
                      <input
                        type="range"
                        min="0.05"
                        max="0.40"
                        step="0.01"
                        value={musicVolume}
                        onChange={(e) => setMusicVolume(parseFloat(e.target.value))}
                        disabled={renderLoading}
                        className="volume-slider"
                      />
                      <p className="option-desc ducking-active">
                        Sidechain Ducking: Music automatically drops when voice is detected!
                      </p>
                    </div>
                  ) : (
                    <p className="option-desc">
                      Add a lo-fi or energetic backing track looped to video length.
                    </p>
                  )}
                </div>

                <div className="render-option-box caption-toggle-box">
                  <div className="render-option-header">
                    <span className="option-title">Visual Styling & Captions</span>
                  </div>
                  <label className="checkbox-label" title="Burn formatted subtitles directly into video pixels">
                    <input
                      type="checkbox"
                      checked={burnSubtitles}
                      onChange={(e) => setBurnSubtitles(e.target.checked)}
                      disabled={renderLoading}
                    />
                    <span>Burn Re-timed Captions into Video</span>
                  </label>
                  <p className="option-desc">
                    Re-aligns subtitle timestamps with the EDL cuts so words match video perfectly.
                  </p>
                </div>

                <div className="render-option-box">
                  <div className="render-option-header">
                    <span className="option-title">Visual Dynamic Zoom</span>
                    <span className="option-badge">Retention</span>
                  </div>
                  <label className="checkbox-label" title="Apply 1.15x punch-in zoom on alternating cuts">
                    <input
                      type="checkbox"
                      checked={enablePunchIn}
                      onChange={(e) => setEnablePunchIn(e.target.checked)}
                      disabled={renderLoading || autoEditLoading}
                    />
                    <span>Dynamic Punch-In Zoom (1.15x)</span>
                  </label>
                  <p className="option-desc">
                    Alternates camera zoom on cuts to eliminate talking-head visual fatigue.
                  </p>
                </div>
              </div>

              {renderLoading && (
                <div className="loading-card render-loading-box">
                  <div className="spinner"></div>
                  <p>Processing frame-accurate cuts, re-timing subtitles, reframing canvas, and ducking audio...</p>
                </div>
              )}

              {renderResult && (
                <div className="rendered-output-container">
                  <div className="render-success-banner">
                    <span className="success-badge-icon">✓</span>
                    <div>
                      <strong>Video Successfully Rendered & Validated!</strong>
                      <p>
                        Duration: <strong>{renderResult.rendered_duration.toFixed(2)}s</strong> (Planned: {renderResult.expected_duration.toFixed(2)}s)
                        {" • "}Size: <strong>{(renderResult.file_size_bytes / 1024).toFixed(1)} KB</strong>
                        {" • "}Aspect: <strong>{aspectRatio === "original" ? "Original" : aspectRatio}</strong>
                        {bgMusic !== "none" && ` • Music: ${bgMusic.replace("_", " ")}`}
                        {enablePunchIn && " • Punch-In Zoom: Active"}
                        {renderResult.burned_subtitles && " • Captions: Burned In"}
                      </p>
                    </div>
                  </div>

                  <div className="rendered-preview-grid">
                    <div className="rendered-player-box">
                      <h3>Final Edited Video Preview</h3>
                      <video
                        key={renderResult.job_id + "-" + renderResult.rendered_duration + "-" + renderResult.burned_subtitles}
                        src={getRenderedVideoUrl(jobData.job_id)}
                        controls
                        className="preview-video"
                      />
                    </div>

                    <div className="rendered-details-box">
                      <h3>Export Deliverables</h3>
                      <div className="export-buttons">
                        <a
                          href={getRenderedVideoUrl(jobData.job_id)}
                          download="final_edit.mp4"
                          className="action-btn action-btn-accent export-download-btn"
                        >
                          ⬇ Download Final Video (.mp4)
                        </a>
                        {renderResult.subtitles_url && (
                          <a
                            href={getSubtitlesDownloadUrl(jobData.job_id)}
                            download="subtitles.srt"
                            className="action-btn export-download-btn"
                          >
                            📄 Download Subtitles (.srt)
                          </a>
                        )}
                      </div>

                      <h3 style={{ marginTop: "1.25rem" }}>Quality Validation Report</h3>
                      <div className="validation-checks-list">
                        {renderResult.validation.checks.map((chk, i) => (
                          <div key={i} className={`val-check-item ${chk.passed ? "passed" : "failed"}`}>
                            <span className="val-icon">{chk.passed ? "✓" : "✗"}</span>
                            <div className="val-info">
                              <span className="val-name">{chk.name}</span>
                              <p className="val-details">{chk.details}</p>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Week 5: Conversational Re-Editing & Revision Loop */}
                  <div className="card revision-card">
                    <div className="revision-header">
                      <h3>💬 Iterative AI Director Feedback (Draft ➔ Feedback ➔ Revision)</h3>
                      <p className="revision-sub">
                        Refine your cut in seconds. Instruct the Director to adjust duration, drop clips, change music, or switch canvas format without re-uploading.
                      </p>
                    </div>
                    <form onSubmit={handleRevise} className="revision-form">
                      <div className="revision-input-row">
                        <input
                          type="text"
                          value={revisionText}
                          onChange={(e) => setRevisionText(e.target.value)}
                          placeholder="e.g. Make it 5 seconds shorter, remove the opening pause, change to upbeat music..."
                          disabled={revisionLoading}
                          className="revision-input"
                        />
                        <button
                          type="submit"
                          disabled={revisionLoading || !revisionText.trim()}
                          className={`action-btn action-btn-accent revision-submit-btn ${revisionLoading ? "loading" : ""}`}
                        >
                          {revisionLoading ? "Revising Cut..." : "⚡ Revise & Re-render"}
                        </button>
                      </div>
                      <div className="quick-suggestions">
                        <span className="quick-label">Quick Ideas:</span>
                        <button
                          type="button"
                          className="quick-chip"
                          onClick={() => setRevisionText("Make it 5 seconds shorter")}
                        >
                          Make it 5s shorter
                        </button>
                        <button
                          type="button"
                          className="quick-chip"
                          onClick={() => setRevisionText("Drop opening pause and keep strong speech")}
                        >
                          Drop opening pause
                        </button>
                        <button
                          type="button"
                          className="quick-chip"
                          onClick={() => setRevisionText("Change background music to upbeat")}
                        >
                          Switch to upbeat music
                        </button>
                        <button
                          type="button"
                          className="quick-chip"
                          onClick={() => setRevisionText("Switch to 1:1 square canvas")}
                        >
                          Convert to 1:1 square
                        </button>
                      </div>
                    </form>
                  </div>
                </div>
              )}
            </div>

            {/* Job Details & Metadata Sidebar/Footer */}
            <div className="card job-status-card">
              <div className="card-header">
                <h2>Job Details</h2>
                <button
                  type="button"
                  onClick={handleRefreshStatus}
                  disabled={statusLoading}
                  className="refresh-btn"
                >
                  {statusLoading ? "Checking..." : "Refresh Status"}
                </button>
              </div>

              <div className="detail-row">
                <span className="label">Job ID:</span>
                <code className="value-code">{jobData.job_id}</code>
              </div>

              <div className="detail-row">
                <span className="label">Status:</span>
                <span className={`status-badge status-${currentStatus}`}>
                  {currentStatus || "unknown"}
                </span>
              </div>
            </div>

            {/* Video Metadata */}
            {metadata && (
              <div className="card metadata-card">
                <h2>Video Metadata</h2>
                <div className="metadata-grid">
                  <div className="metadata-item">
                    <span className="item-label">Duration</span>
                    <span className="item-value">{formatDuration(metadata.duration)}</span>
                  </div>
                  <div className="metadata-item">
                    <span className="item-label">Resolution</span>
                    <span className="item-value">
                      {primaryVideo
                        ? `${primaryVideo.width} × ${primaryVideo.height}`
                        : "N/A"}
                    </span>
                  </div>
                  <div className="metadata-item">
                    <span className="item-label">FPS</span>
                    <span className="item-value">
                      {primaryVideo ? `${primaryVideo.fps.toFixed(2)} fps` : "N/A"}
                    </span>
                  </div>
                  <div className="metadata-item">
                    <span className="item-label">Video Codec</span>
                    <span className="item-value">
                      {primaryVideo ? primaryVideo.codec_name : "N/A"}
                    </span>
                  </div>
                  <div className="metadata-item">
                    <span className="item-label">Audio</span>
                    <span className="item-value">
                      {metadata.has_audio ? (
                        <span className="badge-success">
                          Available ({primaryAudio?.codec_name || "audio"})
                        </span>
                      ) : (
                        <span className="badge-muted">No Audio</span>
                      )}
                    </span>
                  </div>
                  <div className="metadata-item">
                    <span className="item-label">File Size</span>
                    <span className="item-value">
                      {(metadata.size_bytes / 1024).toFixed(1)} KB
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
