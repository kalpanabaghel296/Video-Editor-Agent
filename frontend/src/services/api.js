/**
 * Frontend API client for AI Video Editing Agent.
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

/**
 * Returns the direct stream URL for a job's video.
 */
export function getVideoStreamUrl(jobId) {
  return `${API_BASE_URL}/jobs/${jobId}/video`;
}

/**
 * Upload a video file with an optional editing instruction.
 */
export async function uploadVideo(file, instruction = "") {
  const formData = new FormData();
  formData.append("file", file);
  if (instruction && instruction.trim()) {
    formData.append("instruction", instruction.trim());
  }

  const response = await fetch(`${API_BASE_URL}/upload`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    let errorDetail = `Upload failed with status ${response.status}`;
    try {
      const errorJson = await response.json();
      if (errorJson.detail) {
        errorDetail =
          typeof errorJson.detail === "string"
            ? errorJson.detail
            : JSON.stringify(errorJson.detail);
      }
    } catch (_) {}
    throw new Error(errorDetail);
  }

  return response.json();
}

/**
 * Fetch the current status of a job.
 */
export async function getJobStatus(jobId) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`);

  if (!response.ok) {
    let errorDetail = `Failed to get job status (${response.status})`;
    try {
      const errorJson = await response.json();
      if (errorJson.detail) {
        errorDetail =
          typeof errorJson.detail === "string"
            ? errorJson.detail
            : JSON.stringify(errorJson.detail);
      }
    } catch (_) {}
    throw new Error(errorDetail);
  }

  return response.json();
}

/**
 * Fetch the persisted metadata of a job.
 */
export async function getJobMetadata(jobId) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/metadata`);

  if (!response.ok) {
    let errorDetail = `Failed to get job metadata (${response.status})`;
    try {
      const errorJson = await response.json();
      if (errorJson.detail) {
        errorDetail =
          typeof errorJson.detail === "string"
            ? errorJson.detail
            : JSON.stringify(errorJson.detail);
      }
    } catch (_) {}
    throw new Error(errorDetail);
  }

  return response.json();
}

/**
 * Trigger Whisper speech-to-text transcription.
 */
export async function triggerTranscription(jobId, modelSize = "base", language = "en") {
  let url = `${API_BASE_URL}/jobs/${jobId}/transcribe?model_size=${modelSize}`;
  if (language && language !== "auto") {
    url += `&language=${encodeURIComponent(language.trim())}`;
  }

  const response = await fetch(url, { method: "POST" });

  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Transcription failed (${response.status})`);
  }

  return response.json();
}

/**
 * Retrieve saved transcript for a job.
 */
export async function getTranscript(jobId) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/transcript`);
  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch transcript (${response.status})`);
  }
  return response.json();
}

/**
 * Trigger FFmpeg silence detection.
 */
export async function triggerSilenceDetection(jobId, noiseDb = -26.0, minDuration = 0.3) {
  const response = await fetch(
    `${API_BASE_URL}/jobs/${jobId}/silence/detect?noise_db=${noiseDb}&min_duration=${minDuration}`,
    { method: "POST" }
  );

  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Silence detection failed (${response.status})`);
  }

  return response.json();
}

/**
 * Retrieve saved silence report for a job.
 */
export async function getSilence(jobId) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/silence`);
  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch silence report (${response.status})`);
  }
  return response.json();
}

/**
 * Trigger PySceneDetect scene detection.
 */
export async function triggerSceneDetection(jobId) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/scenes/detect`, {
    method: "POST",
  });

  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Scene detection failed (${response.status})`);
  }

  return response.json();
}

/**
 * Trigger AI Director edit planning (EDL).
 */
export async function triggerPlanning(jobId, instruction = "") {
  let url = `${API_BASE_URL}/jobs/${jobId}/plan`;
  if (instruction && instruction.trim()) {
    url += `?instruction=${encodeURIComponent(instruction.trim())}`;
  }

  const response = await fetch(url, { method: "POST" });

  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `AI Edit Planning failed (${response.status})`);
  }

  return response.json();
}

/**
 * Retrieve saved Edit Decision List (EDL) for a job.
 */
export async function getEditPlan(jobId) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/plan`);
  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch edit plan (${response.status})`);
  }
  return response.json();
}

/**
 * Retrieve list of royalty-free background music tracks.
 */
export async function getMusicTracks() {
  const response = await fetch(`${API_BASE_URL}/jobs/music/tracks`);
  if (!response.ok) {
    return [];
  }
  return response.json();
}

/**
 * Retrieve list of platform editing presets.
 */
export async function getPresets() {
  const response = await fetch(`${API_BASE_URL}/presets`);
  if (!response.ok) {
    return [];
  }
  return response.json();
}

/**
 * Trigger video rendering based on EDL with optional social reframing, punch-in zoom, and background music.
 */
export async function triggerRender(jobId, options = false) {
  let burnSubtitles = false;
  let aspectRatio = "original";
  let bgMusic = null;
  let musicVolume = 0.15;
  let enablePunchIn = false;

  if (typeof options === "boolean") {
    burnSubtitles = options;
  } else if (typeof options === "object" && options !== null) {
    burnSubtitles = !!options.burnSubtitles;
    aspectRatio = options.aspectRatio || "original";
    bgMusic = options.bgMusic || null;
    musicVolume = options.musicVolume ?? 0.15;
    enablePunchIn = !!options.enablePunchIn;
  }

  let url = `${API_BASE_URL}/jobs/${jobId}/render?burn_subtitles=${burnSubtitles}&aspect_ratio=${encodeURIComponent(
    aspectRatio
  )}&music_volume=${musicVolume}`;

  if (bgMusic && bgMusic !== "none") {
    url += `&bg_music=${encodeURIComponent(bgMusic)}`;
  }

  const response = await fetch(url, { method: "POST" });

  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Video rendering failed (${response.status})`);
  }

  return response.json();
}

/**
 * Trigger autonomous end-to-end editing pipeline in a single call.
 */
export async function triggerAutoEdit(jobId, payload = {}) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/auto-edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Autonomous editing failed (${response.status})`);
  }

  return response.json();
}

/**
 * Submit conversational feedback to revise and re-render the edit.
 */
export async function submitRevision(jobId, payload = {}) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/revise`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Revision rendering failed (${response.status})`);
  }

  return response.json();
}

/**
 * Returns stream URL for the rendered final video.
 */
export function getRenderedVideoUrl(jobId) {
  return `${API_BASE_URL}/jobs/${jobId}/output/video`;
}

/**
 * Returns download URL for the generated subtitles.
 */
export function getSubtitlesDownloadUrl(jobId) {
  return `${API_BASE_URL}/jobs/${jobId}/output/subtitles`;
}

/**
 * Retrieve automated quality validation report for rendered video.
 */
export async function getValidationReport(jobId) {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/validation`);
  if (!response.ok) {
    const errorJson = await response.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch validation report (${response.status})`);
  }
  return response.json();
}

