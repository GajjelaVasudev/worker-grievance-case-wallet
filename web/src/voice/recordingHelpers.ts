export const DEFAULT_MAX_UPLOAD_MB = 10;
export const DEFAULT_MAX_DURATION_SECONDS = 30;

const CANDIDATE_RECORDER_MIMES: Array<{ mimeType: string; extension: string }> = [
  { mimeType: "audio/webm;codecs=opus", extension: ".webm" },
  { mimeType: "audio/webm", extension: ".webm" },
  { mimeType: "audio/ogg;codecs=opus", extension: ".ogg" },
  { mimeType: "audio/mp4", extension: ".m4a" },
];

export function formatDuration(seconds: number): string {
  const clamped = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(clamped / 60);
  const secs = clamped % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.max(1, Math.round(kb))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function extensionForMimeType(mimeType: string | null | undefined): string {
  const base = (mimeType ?? "").split(";")[0].trim().toLowerCase();
  if (base === "audio/webm") return ".webm";
  if (base === "audio/ogg" || base === "audio/opus") return ".ogg";
  if (base === "audio/mp4" || base === "audio/m4a" || base === "audio/x-m4a") return ".m4a";
  if (base === "audio/wav" || base === "audio/x-wav" || base === "audio/wave") return ".wav";
  if (base === "audio/mpeg" || base === "audio/mp3") return ".mp3";
  if (base === "audio/flac" || base === "audio/x-flac") return ".flac";
  if (base === "audio/aac" || base === "audio/x-aac") return ".aac";
  return ".webm";
}

export function pickSupportedAudioMimeType(
  isTypeSupported?: (mime: string) => boolean
): { mimeType: string; filename: string } {
  if (isTypeSupported) {
    for (const candidate of CANDIDATE_RECORDER_MIMES) {
      try {
        if (isTypeSupported(candidate.mimeType)) {
          return {
            mimeType: candidate.mimeType,
            filename: `voice-grievance${candidate.extension}`,
          };
        }
      } catch {
        // continue checking fallback candidates
      }
    }
  }
  return { mimeType: "", filename: "voice-grievance.webm" };
}

export function isBrowserRecordingSupported(): boolean {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return false;
  }
  return Boolean(
    navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function" &&
      typeof MediaRecorder !== "undefined"
  );
}

export function validateAudioBeforeUpload(params: {
  sizeBytes: number;
  durationSec: number | null;
  maxUploadMb?: number;
  maxDurationSeconds?: number;
  mimeType?: string | null;
}): string | null {
  const maxUploadMb = params.maxUploadMb ?? DEFAULT_MAX_UPLOAD_MB;
  const maxDurationSeconds = params.maxDurationSeconds ?? DEFAULT_MAX_DURATION_SECONDS;

  if (params.sizeBytes <= 0) {
    return "Recorded audio is empty. Please speak clearly and try recording again.";
  }

  const maxBytes = maxUploadMb * 1024 * 1024;
  if (params.sizeBytes > maxBytes) {
    return `Audio file is too large (${formatFileSize(params.sizeBytes)}). Maximum allowed size is ${maxUploadMb} MB.`;
  }

  if (params.durationSec !== null && Number.isFinite(params.durationSec)) {
    if (params.durationSec <= 0) {
      return "Recording has zero playable duration. Please record again.";
    }
    if (params.durationSec > maxDurationSeconds) {
      return `Recording is too long (${params.durationSec.toFixed(1)}s). Maximum allowed duration is ${maxDurationSeconds} seconds.`;
    }
  }

  const baseMime = (params.mimeType ?? "").split(";")[0].trim().toLowerCase();
  if (baseMime && baseMime !== "application/octet-stream" && !baseMime.startsWith("audio/")) {
    return "Unsupported file format. Please record or upload a valid audio file.";
  }

  return null;
}
