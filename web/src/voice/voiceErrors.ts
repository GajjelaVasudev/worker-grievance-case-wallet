import { ApiError } from "../api/client.ts";

export type VoiceErrorContext = "upload" | "transcribe" | "transcript" | "delete" | "mic";

export function formatVoiceErrorMessage(err: unknown, context: VoiceErrorContext = "transcribe"): string {
  if (err instanceof ApiError) {
    switch (err.errorCode) {
      case "key_not_set":
        return "Sarvam API key is not configured on the server. Your audio is saved — you can replay or delete it, or type a manual transcript below.";
      case "provider_auth_error":
        return "Sarvam authentication failed. Verify the configured SARVAM_API_KEY on the server.";
      case "insufficient_credits":
        return "Sarvam transcription credits are exhausted or unavailable (HTTP 402). Your audio remains saved; enter a manual transcript or top up credits.";
      case "rate_limited":
        return "Sarvam rate limit reached (HTTP 429). Please wait a moment before retrying transcription.";
      case "provider_timeout":
        return "Transcription request to Sarvam timed out. Your audio remains saved; you may retry or enter a manual transcript.";
      case "network_error":
        return "Network failure while contacting the server or transcription service. Check your connection and try again.";
      case "empty_transcript":
        return "Sarvam returned an empty transcript (no clear speech detected). Please try again or enter your transcript manually.";
      case "payload_too_large":
        return "Audio file exceeded the 10 MB / Sarvam payload size limit.";
      case "provider_server_error":
        return "Sarvam transcription service returned a temporary server error. Please try again later.";
      case "invalid_request":
        return err.message || "Sarvam rejected the audio format or request.";
      default:
        break;
    }

    if (err.status === 0) {
      return "Network failure while contacting the server. Check your connection and try again.";
    }
    if (err.status === 402) {
      return "Sarvam transcription credits are exhausted or unavailable (HTTP 402).";
    }
    if (err.status === 413) {
      return "Audio file is too large (exceeds the 10 MB limit).";
    }
    if (err.status === 429) {
      return "Sarvam rate limit reached (HTTP 429). Please wait before retrying.";
    }
    if (err.status === 504) {
      return "Transcription request timed out. Please try again or enter a manual transcript.";
    }
    return err.message;
  }

  if (err && typeof err === "object" && "name" in err) {
    const name = String((err as { name?: unknown }).name ?? "");
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return "Microphone permission was denied. Allow microphone access in your browser settings, or attach a supported audio file (<=10 MB, <=30s).";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "No microphone device was found. Connect a microphone or attach a supported audio file (<=10 MB, <=30s).";
    }
    if (name === "NotSupportedError") {
      return "Audio recording is not supported in this browser or context (requires HTTPS or localhost).";
    }
    if (name === "NotReadableError") {
      return "Could not access microphone because it may be in use by another application.";
    }
  }

  if (err instanceof Error && err.message) {
    return err.message;
  }

  if (context === "mic") {
    return "Unable to start microphone recording in this browser.";
  }
  return "An unexpected voice processing error occurred.";
}
