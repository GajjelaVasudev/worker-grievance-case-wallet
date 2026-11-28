import { ChangeEvent, useEffect, useRef, useState } from "react";
import {
  ApiError,
  CapabilitiesResponse,
  deleteVoiceRecording,
  getVoiceRecordingAudioUrl,
  GrievanceCase,
  transcribeVoiceRecording,
  updateVoiceTranscript,
  uploadVoiceRecording,
  VoiceRecording,
} from "../api/client";
import {
  DEFAULT_MAX_DURATION_SECONDS,
  DEFAULT_MAX_UPLOAD_MB,
  extensionForMimeType,
  formatDuration,
  formatFileSize,
  isBrowserRecordingSupported,
  pickSupportedAudioMimeType,
  validateAudioBeforeUpload,
} from "../voice/recordingHelpers";
import { formatVoiceErrorMessage } from "../voice/voiceErrors";

interface PendingClip {
  blob: Blob;
  url: string;
  filename: string;
  durationSec: number | null;
  source: "mic" | "file";
}

interface VoiceRecorderPanelProps {
  capabilities: CapabilitiesResponse | null;
  selectedCase: GrievanceCase | null;
  onEnsureDraftCase: () => Promise<GrievanceCase>;
  onCaseUpdated: (updated: GrievanceCase) => void;
  onClearGlobalMessages?: () => void;
}

const LANGUAGE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "Auto-detect (Recommended / Code-mix)" },
  { value: "en-IN", label: "English (India)" },
  { value: "hi-IN", label: "Hindi / Hinglish (hi-IN)" },
  { value: "ta-IN", label: "Tamil (ta-IN)" },
  { value: "te-IN", label: "Telugu (te-IN)" },
  { value: "kn-IN", label: "Kannada (kn-IN)" },
  { value: "ml-IN", label: "Malayalam (ml-IN)" },
  { value: "mr-IN", label: "Marathi (mr-IN)" },
  { value: "bn-IN", label: "Bengali (bn-IN)" },
  { value: "gu-IN", label: "Gujarati (gu-IN)" },
];

function originLabel(origin: string): string {
  switch (origin) {
    case "sarvam_asr":
      return "Sarvam AI Transcript";
    case "sarvam_asr_with_worker_edits":
      return "Sarvam AI + Worker Edits";
    case "manual_worker_entry":
      return "Manual Worker Text (Not AI)";
    case "demo_sample":
      return "Demo Sample";
    default:
      return "Not Transcribed";
  }
}

function measureLocalAudioDuration(objectUrl: string): Promise<number | null> {
  return new Promise((resolve) => {
    const audio = document.createElement("audio");
    audio.preload = "metadata";
    const cleanup = () => {
      audio.onloadedmetadata = null;
      audio.onerror = null;
      audio.src = "";
    };
    audio.onloadedmetadata = () => {
      const dur = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : null;
      cleanup();
      resolve(dur);
    };
    audio.onerror = () => {
      cleanup();
      resolve(null);
    };
    audio.src = objectUrl;
  });
}

export function VoiceRecorderPanel({
  capabilities,
  selectedCase,
  onEnsureDraftCase,
  onCaseUpdated,
  onClearGlobalMessages,
}: VoiceRecorderPanelProps) {
  const maxUploadMb = capabilities?.max_upload_mb ?? DEFAULT_MAX_UPLOAD_MB;
  const maxDurationSeconds = capabilities?.max_audio_duration_seconds ?? DEFAULT_MAX_DURATION_SECONDS;
  const asrConfigured = Boolean(capabilities?.asr.configured);
  const micSupported = isBrowserRecordingSupported();

  const [recorderState, setRecorderState] = useState<"idle" | "recording" | "stopped">("idle");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [stoppedAtLimit, setStoppedAtLimit] = useState(false);
  const [pendingClip, setPendingClip] = useState<PendingClip | null>(null);
  const [uploading, setUploading] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceStatus, setVoiceStatus] = useState<string | null>(null);

  const [consentByRec, setConsentByRec] = useState<Record<string, boolean>>({});
  const [languageByRec, setLanguageByRec] = useState<Record<string, string>>({});
  const [transcribingIds, setTranscribingIds] = useState<Record<string, boolean>>({});
  const [deletingIds, setDeletingIds] = useState<Record<string, boolean>>({});
  const [savingTranscriptIds, setSavingTranscriptIds] = useState<Record<string, boolean>>({});
  const [draftEdits, setDraftEdits] = useState<Record<string, string>>({});
  const [recordingErrors, setRecordingErrors] = useState<Record<string, string>>({});

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const startedAtRef = useRef<number>(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const recordings: VoiceRecording[] = [...(selectedCase?.voice_recordings ?? [])].sort(
    (a, b) => a.recording_order - b.recording_order
  );

  // Clean up pending object URL on replace/unmount
  useEffect(() => {
    const currentUrl = pendingClip?.url;
    return () => {
      if (currentUrl) {
        URL.revokeObjectURL(currentUrl);
      }
    };
  }, [pendingClip?.url]);

  // Stop active recording streams/timers cleanly on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
      }
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        mediaRecorderRef.current.stop();
      }
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // Initialize transcript editor text when new transcripts arrive
  useEffect(() => {
    setDraftEdits((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const rec of recordings) {
        if (next[rec.id] === undefined) {
          const initialText = rec.worker_edited_transcript ?? rec.raw_transcript ?? "";
          if (initialText) {
            next[rec.id] = initialText;
            changed = true;
          }
        }
      }
      return changed ? next : prev;
    });
  }, [selectedCase?.id, recordings.length, recordings.map((r) => `${r.id}:${r.raw_transcript ?? ""}:${r.worker_edited_transcript ?? ""}`).join("|")]);

  function stopTracksAndTimer() {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  }

  async function startRecording() {
    onClearGlobalMessages?.();
    setVoiceError(null);
    setVoiceStatus(null);
    setStoppedAtLimit(false);

    if (!micSupported) {
      setVoiceError(
        "Microphone recording is not supported in this browser or context. You can attach a supported audio file (<=10 MB, <=30s) below."
      );
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const picked = pickSupportedAudioMimeType((mime) => MediaRecorder.isTypeSupported(mime));
      const recorder = picked.mimeType
        ? new MediaRecorder(stream, { mimeType: picked.mimeType })
        : new MediaRecorder(stream);

      chunksRef.current = [];
      startedAtRef.current = Date.now();
      setElapsedSeconds(0);
      setPendingClip(null);

      recorder.ondataavailable = (ev: BlobEvent) => {
        if (ev.data && ev.data.size > 0) {
          chunksRef.current.push(ev.data);
        }
      };

      recorder.onerror = () => {
        stopTracksAndTimer();
        setRecorderState("idle");
        setVoiceError("An error occurred while capturing audio from your microphone.");
      };

      recorder.onstop = () => {
        const measuredSec = Math.max(0.1, (Date.now() - startedAtRef.current) / 1000);
        stopTracksAndTimer();

        const resolvedMime =
          recorder.mimeType || picked.mimeType || chunksRef.current[0]?.type || "audio/webm";
        const blob = new Blob(chunksRef.current, { type: resolvedMime });
        const ext = extensionForMimeType(resolvedMime);
        const filename = `voice-grievance-${Date.now()}${ext}`;
        const validationError = validateAudioBeforeUpload({
          sizeBytes: blob.size,
          durationSec: measuredSec,
          maxUploadMb,
          maxDurationSeconds,
          mimeType: blob.type || resolvedMime,
        });

        const url = URL.createObjectURL(blob);
        setPendingClip({
          blob,
          url,
          filename,
          durationSec: Number(measuredSec.toFixed(1)),
          source: "mic",
        });
        setRecorderState("stopped");
        if (validationError) {
          setVoiceError(validationError);
        } else {
          setVoiceStatus(
            "Recording stopped. Review audio playback below, then upload it to your case. Nothing is sent to Sarvam without your explicit consent."
          );
        }
      };

      mediaRecorderRef.current = recorder;
      recorder.start(250);
      setRecorderState("recording");

      // Stop right before crossing the strict 30s backend cutoff and notify explicitly (never silently truncate)
      const autoStopThreshold = Math.max(1, maxDurationSeconds - 0.25);
      timerRef.current = window.setInterval(() => {
        const elapsed = (Date.now() - startedAtRef.current) / 1000;
        setElapsedSeconds(Math.min(maxDurationSeconds, elapsed));
        if (elapsed >= autoStopThreshold && recorder.state === "recording") {
          setStoppedAtLimit(true);
          recorder.stop();
        }
      }, 200);
    } catch (err: unknown) {
      stopTracksAndTimer();
      setRecorderState("idle");
      setVoiceError(formatVoiceErrorMessage(err, "mic"));
    }
  }

  function stopRecording() {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    } else {
      stopTracksAndTimer();
      setRecorderState("idle");
    }
  }

  function discardPendingClip() {
    setPendingClip(null);
    setStoppedAtLimit(false);
    setElapsedSeconds(0);
    setRecorderState("idle");
    setVoiceError(null);
    setVoiceStatus(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  async function handleSelectAudioFile(e: ChangeEvent<HTMLInputElement>) {
    onClearGlobalMessages?.();
    setVoiceError(null);
    setVoiceStatus(null);
    setStoppedAtLimit(false);

    const file = e.target.files?.[0];
    if (!file) return;

    const url = URL.createObjectURL(file);
    const measuredDuration = await measureLocalAudioDuration(url);
    const validationErr = validateAudioBeforeUpload({
      sizeBytes: file.size,
      durationSec: measuredDuration,
      maxUploadMb,
      maxDurationSeconds,
      mimeType: file.type,
    });

    if (validationErr) {
      URL.revokeObjectURL(url);
      setVoiceError(validationErr);
      e.target.value = "";
      return;
    }

    setPendingClip({
      blob: file,
      url,
      filename: file.name || `voice-upload${extensionForMimeType(file.type)}`,
      durationSec: measuredDuration !== null ? Number(measuredDuration.toFixed(1)) : null,
      source: "file",
    });
    setRecorderState("stopped");
    setVoiceStatus("Audio clip ready for playback review. Upload it to your case when ready.");
  }

  async function handleUploadPendingClip() {
    if (!pendingClip || uploading) return;
    onClearGlobalMessages?.();
    setVoiceError(null);
    setVoiceStatus(null);

    const clientCheck = validateAudioBeforeUpload({
      sizeBytes: pendingClip.blob.size,
      durationSec: pendingClip.durationSec,
      maxUploadMb,
      maxDurationSeconds,
      mimeType: pendingClip.blob.type,
    });
    if (clientCheck) {
      setVoiceError(clientCheck);
      return;
    }

    setUploading(true);
    try {
      const targetCase = selectedCase ?? (await onEnsureDraftCase());
      const res = await uploadVoiceRecording(targetCase.id, pendingClip.blob, pendingClip.filename);
      onCaseUpdated(res.case);
      setPendingClip(null);
      setStoppedAtLimit(false);
      setElapsedSeconds(0);
      setRecorderState("idle");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      setVoiceStatus(
        `Saved Recording #${res.recording.recording_order} (${res.recording.measured_duration_sec.toFixed(1)}s) privately to ${res.case.case_code}. Review playback and grant consent below if you want Sarvam transcription.`
      );
    } catch (err: unknown) {
      setVoiceError(formatVoiceErrorMessage(err, "upload"));
    } finally {
      setUploading(false);
    }
  }

  async function handleTranscribe(rec: VoiceRecording) {
    if (!selectedCase || transcribingIds[rec.id]) return;
    onClearGlobalMessages?.();
    setVoiceError(null);
    setVoiceStatus(null);
    setRecordingErrors((prev) => {
      const next = { ...prev };
      delete next[rec.id];
      return next;
    });

    if (!consentByRec[rec.id]) {
      setRecordingErrors((prev) => ({
        ...prev,
        [rec.id]: "Please check the consent box before sending audio to Sarvam.",
      }));
      return;
    }

    setTranscribingIds((prev) => ({ ...prev, [rec.id]: true }));
    try {
      const lang = languageByRec[rec.id]?.trim() || null;
      const res = await transcribeVoiceRecording(selectedCase.id, rec.id, {
        consent_granted: true,
        language_code: lang,
      });
      onCaseUpdated(res.case);

      if (res.recording.transcription_status === "key_not_set") {
        setRecordingErrors((prev) => ({
          ...prev,
          [rec.id]:
            res.recording.error_message ||
            "Sarvam API key is not configured. Audio remains saved; enter a manual transcript below.",
        }));
      } else if (res.recording.raw_transcript) {
        setDraftEdits((prev) => ({
          ...prev,
          [rec.id]: res.recording.worker_edited_transcript ?? res.recording.raw_transcript ?? "",
        }));
        setVoiceStatus(
          `Transcribed Recording #${rec.recording_order} via Sarvam (${res.recording.asr_model ?? "saaras:v4"}). Original transcript is locked read-only; edit separately below if needed.`
        );
      }
    } catch (err: unknown) {
      const friendly = formatVoiceErrorMessage(err, "transcribe");
      setRecordingErrors((prev) => ({ ...prev, [rec.id]: friendly }));
      if (err instanceof ApiError && err.recording && selectedCase) {
        const updatedRecs = selectedCase.voice_recordings.map((item) =>
          item.id === err.recording!.id ? err.recording! : item
        );
        onCaseUpdated({ ...selectedCase, voice_recordings: updatedRecs });
      }
    } finally {
      setTranscribingIds((prev) => ({ ...prev, [rec.id]: false }));
    }
  }

  async function handleSaveTranscript(rec: VoiceRecording) {
    if (!selectedCase || savingTranscriptIds[rec.id]) return;
    onClearGlobalMessages?.();
    setVoiceError(null);
    setVoiceStatus(null);
    setRecordingErrors((prev) => {
      const next = { ...prev };
      delete next[rec.id];
      return next;
    });

    const text = (draftEdits[rec.id] ?? "").trim();
    if (!text) {
      setRecordingErrors((prev) => ({
        ...prev,
        [rec.id]: "Transcript text cannot be empty before saving.",
      }));
      return;
    }

    setSavingTranscriptIds((prev) => ({ ...prev, [rec.id]: true }));
    try {
      const res = await updateVoiceTranscript(selectedCase.id, rec.id, text);
      onCaseUpdated(res.case);
      setDraftEdits((prev) => ({
        ...prev,
        [rec.id]: res.recording.worker_edited_transcript ?? text,
      }));
      setVoiceStatus(
        res.recording.raw_transcript
          ? `Saved your edited transcript for Recording #${rec.recording_order}. Original Sarvam transcript remains unchanged.`
          : `Saved manual worker transcript for Recording #${rec.recording_order}.`
      );
    } catch (err: unknown) {
      setRecordingErrors((prev) => ({
        ...prev,
        [rec.id]: formatVoiceErrorMessage(err, "transcript"),
      }));
    } finally {
      setSavingTranscriptIds((prev) => ({ ...prev, [rec.id]: false }));
    }
  }

  async function handleDeleteRecording(rec: VoiceRecording) {
    if (!selectedCase || deletingIds[rec.id]) return;
    onClearGlobalMessages?.();
    setVoiceError(null);
    setVoiceStatus(null);
    setDeletingIds((prev) => ({ ...prev, [rec.id]: true }));
    try {
      const res = await deleteVoiceRecording(selectedCase.id, rec.id);
      onCaseUpdated(res.case);
      setVoiceStatus(`Deleted Recording #${rec.recording_order} and its stored audio file.`);
    } catch (err: unknown) {
      setRecordingErrors((prev) => ({
        ...prev,
        [rec.id]: formatVoiceErrorMessage(err, "delete"),
      }));
    } finally {
      setDeletingIds((prev) => ({ ...prev, [rec.id]: false }));
    }
  }

  return (
    <section
      aria-label="Voice Grievance Recording and Transcription"
      className="rounded border border-slate-300 bg-white p-4 space-y-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-200 pb-3">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <span aria-hidden="true">🎙</span>
            <span>Voice Grievance Recorder &amp; Sarvam Transcription</span>
          </h2>
          <p className="text-xs text-slate-600">
            Speak in your own words (up to {maxDurationSeconds}s, {maxUploadMb} MB max). Review playback first — audio is never sent to Sarvam without your explicit consent.
          </p>
        </div>
        <div className="text-xs text-right">
          <span
            className={`inline-block rounded px-2 py-0.5 font-medium ${
              asrConfigured ? "bg-emerald-100 text-emerald-900" : "bg-amber-100 text-amber-900"
            }`}
          >
            {asrConfigured
              ? `Sarvam Ready (${capabilities?.asr.model ?? "saaras:v4"})`
              : "Sarvam API Key Not Set"}
          </span>
          <div className="text-[11px] text-slate-500 mt-1">
            Target case: {selectedCase ? `${selectedCase.case_code} (${selectedCase.status})` : "New draft auto-created on upload"}
          </div>
        </div>
      </div>

      {capabilities && !asrConfigured && (
        <div
          role="status"
          className="rounded border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900"
        >
          <strong>Missing Sarvam API key ({capabilities.asr.status_message}):</strong> Actual AI
          transcription requires a configured Sarvam API key on the server. You can still record,
          replay, upload, and delete audio clips, or write a manual worker transcript (never labeled
          as AI-generated).
        </div>
      )}

      {!micSupported && (
        <div className="rounded border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900">
          Browser microphone recording is unavailable here (requires HTTPS or localhost with
          MediaRecorder support). You can still attach a supported audio file (&le;{maxDurationSeconds}s, &le;{maxUploadMb} MB).
        </div>
      )}

      {voiceError && (
        <div role="alert" className="rounded border border-red-300 bg-red-50 p-2.5 text-xs text-red-900">
          {voiceError}
        </div>
      )}

      {voiceStatus && (
        <div role="status" className="rounded border border-emerald-300 bg-emerald-50 p-2.5 text-xs text-emerald-900">
          {voiceStatus}
        </div>
      )}

      {/* Primary Voice Recording Controls */}
      <div className="rounded border border-slate-200 bg-slate-50 p-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {recorderState !== "recording" ? (
            <button
              type="button"
              onClick={startRecording}
              disabled={!micSupported || uploading}
              className="rounded bg-rose-700 hover:bg-rose-800 disabled:bg-slate-300 px-4 py-2 text-sm font-medium text-white flex items-center gap-2"
            >
              <span aria-hidden="true">🎤</span>
              <span>Start Recording</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={stopRecording}
              className="rounded bg-slate-900 hover:bg-slate-800 px-4 py-2 text-sm font-medium text-white flex items-center gap-2"
            >
              <span aria-hidden="true">⏹</span>
              <span>Stop Recording</span>
            </button>
          )}

          <div className="text-xs space-y-0.5">
            <div className="flex items-center gap-2 font-medium">
              <span
                className={`inline-block h-2.5 w-2.5 rounded-full ${
                  recorderState === "recording"
                    ? "bg-rose-600 animate-pulse"
                    : recorderState === "stopped"
                    ? "bg-emerald-600"
                    : "bg-slate-400"
                }`}
              />
              <span>
                {recorderState === "recording"
                  ? "Recording in progress..."
                  : recorderState === "stopped"
                  ? "Clip ready for review"
                  : "Microphone idle"}
              </span>
            </div>
            <div className="font-mono text-slate-700" data-testid="recording-timer">
              {formatDuration(elapsedSeconds)} / {formatDuration(maxDurationSeconds)} max
            </div>
          </div>
        </div>

        <label className="text-xs text-slate-700 flex flex-col gap-1">
          <span>Or attach audio file (&le;{maxDurationSeconds}s, &le;{maxUploadMb} MB):</span>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,.webm,.ogg,.wav,.mp3,.m4a,.flac,.aac"
            onChange={handleSelectAudioFile}
            disabled={recorderState === "recording" || uploading}
            className="text-xs"
          />
        </label>
      </div>

      {stoppedAtLimit && (
        <div className="rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          Recording reached the {maxDurationSeconds}-second maximum limit and stopped automatically
          (recordings are never silently truncated). Listen below before uploading or re-record a shorter clip.
        </div>
      )}

      {/* Unsaved Recording Preview */}
      {pendingClip && (
        <div className="rounded border border-blue-300 bg-blue-50 p-3 space-y-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="font-semibold text-blue-950">
              Preview Clip ({pendingClip.source === "mic" ? "Microphone" : "Selected File"} &mdash; Not Uploaded Yet)
            </div>
            <div className="text-blue-900">
              {pendingClip.filename} &middot;{" "}
              {pendingClip.durationSec !== null ? `${pendingClip.durationSec.toFixed(1)}s` : "duration checked on upload"}{" "}
              &middot; {formatFileSize(pendingClip.blob.size)}
            </div>
          </div>

          <audio controls src={pendingClip.url} className="w-full" />

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleUploadPendingClip}
              disabled={uploading}
              className="rounded bg-slate-900 hover:bg-slate-800 disabled:bg-slate-400 px-3 py-1.5 text-xs font-medium text-white"
            >
              {uploading
                ? "Validating & Uploading..."
                : selectedCase
                ? `Upload Recording to ${selectedCase.case_code}`
                : "Create Draft Case & Upload Recording"}
            </button>
            <button
              type="button"
              onClick={discardPendingClip}
              disabled={uploading}
              className="rounded border border-slate-400 bg-white px-3 py-1.5 text-xs text-slate-800"
            >
              Discard Clip
            </button>
          </div>
        </div>
      )}

      {/* Stored Case Recordings & Sarvam Transcription Workflow */}
      {selectedCase && (
        <div className="space-y-3 pt-1">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              Saved Recordings for {selectedCase.case_code} ({recordings.length})
            </h3>
          </div>

          {recordings.length === 0 ? (
            <p className="text-xs text-slate-600">
              No voice recordings saved on this case yet. Click Start Recording above to speak your grievance.
            </p>
          ) : (
            <ul className="space-y-3">
              {recordings.map((rec) => {
                const isTranscribing = Boolean(transcribingIds[rec.id]);
                const isDeleting = Boolean(deletingIds[rec.id]);
                const isSavingTranscript = Boolean(savingTranscriptIds[rec.id]);
                const consentChecked = Boolean(consentByRec[rec.id]);
                const recError = recordingErrors[rec.id] || (rec.transcription_status === "failed" ? rec.error_message : null);
                const draftText = draftEdits[rec.id] ?? rec.worker_edited_transcript ?? rec.raw_transcript ?? "";
                const canSaveEdit =
                  draftText.trim().length > 0 &&
                  draftText.trim() !== (rec.worker_edited_transcript ?? "").trim();

                return (
                  <li key={rec.id} className="rounded border border-slate-300 p-3 space-y-2.5 bg-slate-50">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="text-xs space-y-0.5">
                        <div className="font-semibold text-slate-900">
                          Recording #{rec.recording_order} &middot; {rec.original_filename}
                        </div>
                        <div className="text-slate-600">
                          {rec.measured_duration_sec.toFixed(1)}s &middot; {formatFileSize(rec.size_bytes)} &middot;{" "}
                          {rec.mime_type}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="rounded bg-slate-200 px-2 py-0.5 font-medium text-slate-800">
                          Status: {rec.transcription_status}
                        </span>
                        <span className="rounded bg-indigo-100 px-2 py-0.5 font-medium text-indigo-900">
                          {originLabel(rec.transcript_origin)}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleDeleteRecording(rec)}
                          disabled={isDeleting || isTranscribing}
                          className="rounded border border-red-300 bg-white px-2 py-0.5 text-red-700 disabled:opacity-50"
                        >
                          {isDeleting ? "Deleting..." : "Delete Recording"}
                        </button>
                      </div>
                    </div>

                    <audio
                      controls
                      preload="metadata"
                      src={getVoiceRecordingAudioUrl(selectedCase.id, rec.id)}
                      className="w-full"
                    />

                    {recError && (
                      <div role="alert" className="rounded border border-red-300 bg-red-50 p-2 text-xs text-red-900">
                        {recError}
                      </div>
                    )}

                    {/* Consent-Gated Sarvam Transcription (only while no immutable raw_transcript exists) */}
                    {rec.raw_transcript === null && (
                      <div className="rounded border border-slate-200 bg-white p-2.5 space-y-2 text-xs">
                        <div className="font-medium text-slate-900">
                          Step 3: Approve &amp; Transcribe via Sarvam AI
                        </div>
                        <label className="flex items-start gap-2 text-slate-800 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={consentChecked}
                            disabled={isTranscribing}
                            onChange={(e) =>
                              setConsentByRec((prev) => ({ ...prev, [rec.id]: e.target.checked }))
                            }
                            className="mt-0.5"
                          />
                          <span>
                            I explicitly consent to sending Recording #{rec.recording_order} to Sarvam AI (
                            {capabilities?.asr.model ?? "saaras:v4"}) for speech-to-text transcription.
                          </span>
                        </label>

                        <div className="flex flex-wrap items-center gap-2">
                          <label className="flex items-center gap-1.5 text-slate-700">
                            <span>Language:</span>
                            <select
                              value={languageByRec[rec.id] ?? ""}
                              disabled={isTranscribing}
                              onChange={(e) =>
                                setLanguageByRec((prev) => ({ ...prev, [rec.id]: e.target.value }))
                              }
                              className="rounded border border-slate-300 p-1 text-xs"
                            >
                              {LANGUAGE_OPTIONS.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          </label>

                          <button
                            type="button"
                            onClick={() => handleTranscribe(rec)}
                            disabled={!consentChecked || isTranscribing || !asrConfigured}
                            className="rounded bg-indigo-700 hover:bg-indigo-800 disabled:bg-slate-300 px-3 py-1.5 font-medium text-white"
                          >
                            {isTranscribing ? "Transcribing via Sarvam..." : "Transcribe with Sarvam"}
                          </button>
                        </div>

                        {!asrConfigured && (
                          <p className="text-[11px] text-amber-800">
                            Sarvam transcription is unavailable until a Sarvam API key is configured on the server.
                          </p>
                        )}
                      </div>
                    )}

                    {/* Immutable Original Sarvam Transcript */}
                    {rec.raw_transcript !== null && (
                      <div className="rounded border border-emerald-300 bg-emerald-50 p-2.5 space-y-1 text-xs">
                        <div className="flex flex-wrap items-center justify-between gap-2 font-semibold text-emerald-950">
                          <span>Original Sarvam Transcript (Immutable &mdash; Read-Only)</span>
                          <span className="font-normal text-[11px] text-emerald-900">
                            {rec.asr_model ?? "saaras:v4"}
                            {rec.detected_language ? ` · Lang: ${rec.detected_language}` : ""}
                            {rec.asr_transcribed_at
                              ? ` · ${new Date(rec.asr_transcribed_at).toLocaleString()}`
                              : ""}
                          </span>
                        </div>
                        <p
                          data-testid={`raw-transcript-${rec.id}`}
                          className="whitespace-pre-wrap rounded bg-white/80 p-2 text-slate-900 border border-emerald-200"
                        >
                          {rec.raw_transcript}
                        </p>
                      </div>
                    )}

                    {/* Separate Worker-Edited / Manual Transcript */}
                    <div className="rounded border border-slate-200 bg-white p-2.5 space-y-2 text-xs">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <label htmlFor={`transcript-edit-${rec.id}`} className="font-semibold text-slate-900">
                          {rec.raw_transcript !== null
                            ? "Worker-Edited Transcript (Separate Version)"
                            : "Manual Worker Transcript (Not AI-Generated)"}
                        </label>
                        {rec.worker_edited_at && (
                          <span className="text-[11px] text-slate-500">
                            Saved {new Date(rec.worker_edited_at).toLocaleString()}
                          </span>
                        )}
                      </div>

                      <textarea
                        id={`transcript-edit-${rec.id}`}
                        rows={3}
                        value={draftText}
                        onChange={(e) =>
                          setDraftEdits((prev) => ({ ...prev, [rec.id]: e.target.value }))
                        }
                        placeholder={
                          rec.raw_transcript !== null
                            ? "Edit or clarify the transcript if needed. Saving creates a separate worker-edited version and leaves the original Sarvam transcript intact."
                            : "Optional: enter a manual transcript (labeled as manual worker entry, never as an AI transcript)."
                        }
                        className="w-full rounded border border-slate-300 p-2 text-sm"
                      />

                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[11px] text-slate-500">
                          {rec.raw_transcript !== null
                            ? "Original Sarvam transcript is never overwritten."
                            : "Manual text is never labeled as Sarvam AI output."}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleSaveTranscript(rec)}
                          disabled={!canSaveEdit || isSavingTranscript}
                          className="rounded bg-slate-900 hover:bg-slate-800 disabled:bg-slate-300 px-3 py-1.5 font-medium text-white"
                        >
                          {isSavingTranscript
                            ? "Saving..."
                            : rec.raw_transcript !== null
                            ? "Save Edited Transcript"
                            : "Save Manual Transcript"}
                        </button>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
