import assert from "node:assert/strict";
import test from "node:test";
import {
  ApiError,
  deleteVoiceRecording,
  getVoiceRecordingAudioUrl,
  transcribeVoiceRecording,
  updateVoiceTranscript,
  uploadVoiceRecording,
} from "../src/api/client.ts";
import {
  extensionForMimeType,
  formatDuration,
  formatFileSize,
  pickSupportedAudioMimeType,
  validateAudioBeforeUpload,
} from "../src/voice/recordingHelpers.ts";
import { formatVoiceErrorMessage } from "../src/voice/voiceErrors.ts";

test("audio helper selects supported browser MIME type and file extension", () => {
  const chromePick = pickSupportedAudioMimeType((m) => m === "audio/webm;codecs=opus");
  assert.equal(chromePick.mimeType, "audio/webm;codecs=opus");
  assert.equal(chromePick.filename, "voice-grievance.webm");

  const firefoxPick = pickSupportedAudioMimeType((m) => m === "audio/ogg;codecs=opus");
  assert.equal(firefoxPick.mimeType, "audio/ogg;codecs=opus");
  assert.equal(firefoxPick.filename, "voice-grievance.ogg");

  const safariPick = pickSupportedAudioMimeType((m) => m === "audio/mp4");
  assert.equal(safariPick.mimeType, "audio/mp4");
  assert.equal(safariPick.filename, "voice-grievance.m4a");

  assert.equal(extensionForMimeType("audio/webm;codecs=opus"), ".webm");
  assert.equal(extensionForMimeType("audio/x-m4a"), ".m4a");
  assert.equal(formatDuration(12.9), "00:12");
  assert.equal(formatFileSize(2048), "2 KB");
});

test("validateAudioBeforeUpload enforces 10 MB and 30-second limits without silent truncation", () => {
  assert.match(
    validateAudioBeforeUpload({ sizeBytes: 0, durationSec: 2.0 }) ?? "",
    /empty/i
  );
  assert.match(
    validateAudioBeforeUpload({
      sizeBytes: 10 * 1024 * 1024 + 1,
      durationSec: 12.0,
      maxUploadMb: 10,
      maxDurationSeconds: 30,
      mimeType: "audio/webm",
    }) ?? "",
    /too large.*10 MB/i
  );
  assert.match(
    validateAudioBeforeUpload({
      sizeBytes: 50000,
      durationSec: 30.5,
      maxUploadMb: 10,
      maxDurationSeconds: 30,
      mimeType: "audio/webm",
    }) ?? "",
    /too long.*30 seconds/i
  );
  assert.match(
    validateAudioBeforeUpload({
      sizeBytes: 50000,
      durationSec: 5.0,
      mimeType: "video/mp4",
    }) ?? "",
    /unsupported/i
  );
  assert.equal(
    validateAudioBeforeUpload({
      sizeBytes: 128000,
      durationSec: 14.2,
      maxUploadMb: 10,
      maxDurationSeconds: 30,
      mimeType: "audio/webm;codecs=opus",
    }),
    null
  );
});

test("formatVoiceErrorMessage maps all Sarvam, upload, network, and mic error states", () => {
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "key", status: 503, errorCode: "key_not_set" })),
    /not configured/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "auth", status: 502, errorCode: "provider_auth_error" })),
    /authentication failed/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "credits", status: 402, errorCode: "insufficient_credits" })),
    /credits/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "limit", status: 429, errorCode: "rate_limited" })),
    /rate limit/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "timeout", status: 504, errorCode: "provider_timeout" })),
    /timed out/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "empty", status: 422, errorCode: "empty_transcript" })),
    /empty transcript/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "net", status: 0, errorCode: "network_error" })),
    /network failure/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "big", status: 413, errorCode: "payload_too_large" })),
    /10 MB|payload/i
  );
  assert.match(formatVoiceErrorMessage({ name: "NotAllowedError" }, "mic"), /permission/i);
  assert.match(formatVoiceErrorMessage({ name: "NotFoundError" }, "mic"), /no microphone/i);
  assert.match(formatVoiceErrorMessage({ name: "NotSupportedError" }, "mic"), /not supported/i);
});

test("voice API client functions reuse backend endpoints and preserve ApiError recording state", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; method: string; body?: unknown }> = [];

  try {
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body });

      if (url.endsWith("/voice") && method === "POST") {
        return new Response(
          JSON.stringify({
            recording: { id: "rec-1", transcription_status: "pending", raw_transcript: null },
            case: { id: "case-1", voice_recordings: [{ id: "rec-1" }] },
          }),
          { status: 201 }
        );
      }
      if (url.endsWith("/transcribe") && method === "POST") {
        return new Response(
          JSON.stringify({
            error_code: "rate_limited",
            detail: "Sarvam rate limit reached (HTTP 429).",
            retryable: true,
            recording: { id: "rec-1", transcription_status: "failed", error_code: "rate_limited" },
          }),
          { status: 429 }
        );
      }
      if (url.endsWith("/transcript") && method === "PATCH") {
        return new Response(
          JSON.stringify({
            recording: {
              id: "rec-1",
              raw_transcript: "Original AI text",
              worker_edited_transcript: "Edited worker text",
            },
            case: { id: "case-1", voice_recordings: [] },
          }),
          { status: 200 }
        );
      }
      if (url.endsWith("/voice/rec-1") && method === "DELETE") {
        return new Response(
          JSON.stringify({ deleted: true, recording_id: "rec-1", case: { id: "case-1", voice_recordings: [] } }),
          { status: 200 }
        );
      }
      return new Response("{}", { status: 404 });
    }) as typeof fetch;

    assert.equal(getVoiceRecordingAudioUrl("case-1", "rec-1"), "/api/cases/case-1/voice/rec-1/audio");

    const uploaded = await uploadVoiceRecording("case-1", new Blob(["abc"], { type: "audio/webm" }), "clip.webm");
    assert.equal(uploaded.recording.id, "rec-1");
    assert.equal(calls[0].url, "/api/cases/case-1/voice");

    await assert.rejects(
      () => transcribeVoiceRecording("case-1", "rec-1", { consent_granted: true }),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 429);
        assert.equal(err.errorCode, "rate_limited");
        assert.equal(err.recording?.transcription_status, "failed");
        return true;
      }
    );

    const edited = await updateVoiceTranscript("case-1", "rec-1", "Edited worker text");
    assert.equal(edited.recording.raw_transcript, "Original AI text");
    assert.equal(edited.recording.worker_edited_transcript, "Edited worker text");

    const deleted = await deleteVoiceRecording("case-1", "rec-1");
    assert.equal(deleted.deleted, true);

    globalThis.fetch = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch;
    await assert.rejects(
      () => uploadVoiceRecording("case-1", new Blob(["abc"], { type: "audio/webm" }), "clip.webm"),
      (err: unknown) => err instanceof ApiError && err.errorCode === "network_error"
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
