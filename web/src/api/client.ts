export type PlatformName = "swiggy" | "zomato" | "rapido" | "uber" | "other";

export type EvidenceCategory =
  | "payout_summary"
  | "incentive_offer"
  | "order_or_trip_records"
  | "support_communication"
  | "other";

export interface ProviderStatus {
  provider: string;
  configured: boolean;
  status_code: string;
  status_message: string;
  model: string | null;
}

export interface CapabilitiesResponse {
  app_name: string;
  phase: string;
  wallet_mode: string;
  public_demo_mode: boolean;
  hero_category: string;
  supported_platforms: string[];
  supported_audio_mime_types: string[];
  max_upload_mb: number;
  max_audio_duration_seconds: number;
  asr: ProviderStatus;
  llm: ProviderStatus;
}

export interface HealthResponse {
  status: string;
  database: string;
  storage: string;
  local_wallet_id: string;
}

export interface PaymentDiscrepancyDetails {
  platform: PlatformName | null;
  work_city: string | null;
  payout_cycle_raw: string | null;
  payout_cycle_start: string | null;
  payout_cycle_end: string | null;
  incentive_scheme_name: string | null;
  expected_amount_inr: number | null;
  received_amount_inr: number | null;
  discrepancy_amount_inr: number | null;
  currency?: string | null;
  work_completed_summary?: string | null;
  affected_order_or_trip_ids: string[] | null;
  prior_ticket_id: string | null;
  support_contact_summary?: string | null;
  support_response_summary?: string | null;
  issue_description: string | null;
}

export interface MissingFieldItem {
  field: string;
  importance: string;
  prompt: string;
}

export interface EvidenceItem {
  id: string;
  case_id: string;
  evidence_category: EvidenceCategory;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256_hash: string;
  worker_note: string | null;
  uploaded_at: string;
}

export interface ChecklistItem {
  key: EvidenceCategory;
  label: string;
  priority: "essential" | "helpful";
  status: "uploaded" | "missing";
  uploaded_count: number;
  guidance: string;
}

export interface EvidenceChecklist {
  category: string;
  supported: boolean;
  items: ChecklistItem[];
  essential_total: number;
  essential_uploaded: number;
  essential_missing: number;
  helpful_total: number;
  helpful_uploaded: number;
  helpful_missing: number;
  ready_for_escalation: boolean;
}

export interface RoutingChannel {
  id: string;
  platform: string;
  category: string;
  jurisdiction: string;
  tier: string;
  channel_name: string;
  contact_type: string;
  contact_value: string;
  instructions: string;
  expected_sla_days: number | null;
  verification_status: string;
  source_url: string;
  what_source_confirms: string;
  last_verified_at: string;
  disclaimer_text: string;
}

export interface RoutingResult {
  route_status: "verified" | "requires_verification";
  status_message: string;
  reason: string | null;
  platform_channels: RoutingChannel[];
  statutory_channels: RoutingChannel[];
}

export interface VoiceRecording {
  id: string;
  case_id: string;
  recording_order: number;
  original_filename: string;
  mime_type: string;
  codec_name: string | null;
  size_bytes: number;
  sha256_hash: string;
  measured_duration_sec: number;
  asr_provider: string;
  asr_model: string | null;
  asr_request_id: string | null;
  transcription_status: "pending" | "completed" | "failed" | "key_not_set" | string;
  transcript_origin:
    | "demo_sample"
    | "sarvam_asr"
    | "sarvam_asr_with_worker_edits"
    | "manual_worker_entry"
    | "none"
    | string;
  detected_language: string | null;
  language_probability: number | null;
  raw_transcript: string | null;
  asr_transcribed_at: string | null;
  worker_edited_transcript: string | null;
  worker_edited_at: string | null;
  effective_transcript: string | null;
  is_demo_sample: boolean;
  sarvam_consent_granted: boolean;
  sarvam_consented_at: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface TimelineEvent {
  id: string;
  event_type: string;
  event_source: "system" | "worker";
  title: string;
  notes: string | null;
  external_reference_id: string | null;
  routing_channel_id: string | null;
  linked_evidence_ids: string[] | null;
  snapshot_payload: Record<string, unknown> | null;
  occurred_at: string;
  created_at: string;
}

export interface GrievanceCase {
  id: string;
  case_code: string;
  worker_id: string;
  category: string;
  platform: PlatformName;
  jurisdiction_state: string | null;
  status: string;
  summary_title: string | null;
  draft_details: PaymentDiscrepancyDetails | null;
  confirmed_details: PaymentDiscrepancyDetails | null;
  confirmed_at: string | null;
  worker_confirmation_note: string | null;
  missing_fields: MissingFieldItem[];
  evidence_items: EvidenceItem[];
  evidence_checklist: EvidenceChecklist;
  routing: RoutingResult;
  voice_recordings: VoiceRecording[];
  timeline: TimelineEvent[];
  created_at: string;
  updated_at: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly errorCode: string | null;
  readonly retryable: boolean;
  readonly recording?: VoiceRecording;

  constructor(params: {
    message: string;
    status: number;
    errorCode?: string | null;
    retryable?: boolean;
    recording?: VoiceRecording;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.status = params.status;
    this.errorCode = params.errorCode ?? null;
    this.retryable = Boolean(params.retryable);
    this.recording = params.recording;
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new ApiError({
      message: "Network failure while contacting the server. Check your connection and try again.",
      status: 0,
      errorCode: "network_error",
      retryable: true,
    });
  }

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let errorCode: string | null = null;
    let retryable = false;
    let recording: VoiceRecording | undefined;
    try {
      const errBody = await response.json();
      if (typeof errBody?.detail === "string") {
        message = errBody.detail;
      } else if (Array.isArray(errBody?.detail) && errBody.detail[0]?.msg) {
        message = errBody.detail[0].msg;
      }
      if (typeof errBody?.error_code === "string") {
        errorCode = errBody.error_code;
      }
      if (typeof errBody?.retryable === "boolean") {
        retryable = errBody.retryable;
      }
      if (errBody?.recording && typeof errBody.recording === "object") {
        recording = errBody.recording as VoiceRecording;
      }
    } catch {
      // keep status fallback
    }
    throw new ApiError({ message, status: response.status, errorCode, retryable, recording });
  }
  return response.json();
}

export function fetchHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/health");
}

export function fetchCapabilities(): Promise<CapabilitiesResponse> {
  return requestJson<CapabilitiesResponse>("/api/capabilities");
}

export async function listCases(): Promise<GrievanceCase[]> {
  const data = await requestJson<{ cases: GrievanceCase[] }>("/api/cases");
  return data.cases;
}

export async function createPaymentCase(payload: {
  platform: PlatformName;
  jurisdiction_state: string | null;
  summary_title: string | null;
  details: PaymentDiscrepancyDetails;
}): Promise<GrievanceCase> {
  const data = await requestJson<{ case: GrievanceCase }>("/api/cases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category: "payment_discrepancy", ...payload }),
  });
  return data.case;
}

export async function updateDraftCase(
  caseId: string,
  payload: {
    platform: PlatformName;
    jurisdiction_state: string | null;
    summary_title: string | null;
    details: PaymentDiscrepancyDetails;
  }
): Promise<GrievanceCase> {
  const data = await requestJson<{ case: GrievanceCase }>(`/api/cases/${caseId}/draft`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return data.case;
}

export async function confirmCase(
  caseId: string,
  payload: {
    confirm: boolean;
    platform: PlatformName;
    jurisdiction_state: string | null;
    summary_title: string | null;
    details: PaymentDiscrepancyDetails;
    worker_confirmation_note: string | null;
  }
): Promise<GrievanceCase> {
  const data = await requestJson<{ case: GrievanceCase }>(`/api/cases/${caseId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return data.case;
}

export async function amendConfirmedCase(
  caseId: string,
  payload: {
    platform: PlatformName;
    jurisdiction_state: string | null;
    summary_title: string | null;
    details: PaymentDiscrepancyDetails;
    amendment_reason: string;
  }
): Promise<GrievanceCase> {
  const data = await requestJson<{ case: GrievanceCase }>(`/api/cases/${caseId}/confirm`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return data.case;
}

export async function uploadEvidenceFile(
  caseId: string,
  evidenceCategory: EvidenceCategory,
  file: File,
  workerNote: string
): Promise<GrievanceCase> {
  const form = new FormData();
  form.append("evidence_category", evidenceCategory);
  if (workerNote.trim()) {
    form.append("worker_note", workerNote.trim());
  }
  form.append("file", file);
  const data = await requestJson<{ case: GrievanceCase }>(`/api/cases/${caseId}/evidence`, {
    method: "POST",
    body: form,
  });
  return data.case;
}

export async function deleteEvidenceFile(caseId: string, evidenceId: string): Promise<GrievanceCase> {
  const data = await requestJson<{ case: GrievanceCase }>(
    `/api/cases/${caseId}/evidence/${evidenceId}`,
    { method: "DELETE" }
  );
  return data.case;
}

export async function uploadVoiceRecording(
  caseId: string,
  file: Blob,
  filename: string
): Promise<{ recording: VoiceRecording; case: GrievanceCase }> {
  const form = new FormData();
  form.append("file", file, filename);
  return requestJson<{ recording: VoiceRecording; case: GrievanceCase }>(`/api/cases/${caseId}/voice`, {
    method: "POST",
    body: form,
  });
}

export function getVoiceRecordingAudioUrl(caseId: string, recordingId: string): string {
  return `/api/cases/${caseId}/voice/${recordingId}/audio`;
}

export async function transcribeVoiceRecording(
  caseId: string,
  recordingId: string,
  payload: { consent_granted: boolean; language_code?: string | null }
): Promise<{ recording: VoiceRecording; case: GrievanceCase }> {
  return requestJson<{ recording: VoiceRecording; case: GrievanceCase }>(
    `/api/cases/${caseId}/voice/${recordingId}/transcribe`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
}

export async function updateVoiceTranscript(
  caseId: string,
  recordingId: string,
  workerEditedTranscript: string
): Promise<{ recording: VoiceRecording; case: GrievanceCase }> {
  return requestJson<{ recording: VoiceRecording; case: GrievanceCase }>(
    `/api/cases/${caseId}/voice/${recordingId}/transcript`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ worker_edited_transcript: workerEditedTranscript }),
    }
  );
}

export async function deleteVoiceRecording(
  caseId: string,
  recordingId: string
): Promise<{ deleted: boolean; recording_id: string; case: GrievanceCase }> {
  return requestJson<{ deleted: boolean; recording_id: string; case: GrievanceCase }>(
    `/api/cases/${caseId}/voice/${recordingId}`,
    { method: "DELETE" }
  );
}

export async function appendMilestone(
  caseId: string,
  payload: {
    event_type:
      | "worker_reported_submission"
      | "evidence_backed_submission"
      | "platform_acknowledgement_reported"
      | "platform_response_reported"
      | "escalation_reported"
      | "closed"
      | "note_added";
    title: string;
    notes: string | null;
    external_reference_id: string | null;
    linked_evidence_ids: string[] | null;
  }
): Promise<GrievanceCase> {
  const data = await requestJson<{ case: GrievanceCase }>(`/api/cases/${caseId}/timeline`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return data.case;
}

export async function deleteCase(caseId: string): Promise<void> {
  await requestJson<{ deleted: boolean }>(`/api/cases/${caseId}`, { method: "DELETE" });
}
