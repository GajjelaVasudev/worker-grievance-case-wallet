import assert from "node:assert/strict";
import test from "node:test";
import {
  ApiError,
  extractVoiceCaseDraft,
  type ExtractionDraft,
  type FieldProvenanceEntry,
  type PaymentDiscrepancyDetails,
  type VoiceRecording,
} from "../src/api/client.ts";
import {
  collectLatestRejectedClaims,
  collectUnresolvedConflicts,
  groupMissingAndFollowupItems,
  markFieldAsWorkerEdited,
  resolveConflictIntoDetails,
  sanitizeProvenanceMap,
} from "../src/voice/extractionHelpers.ts";
import { formatVoiceErrorMessage } from "../src/voice/voiceErrors.ts";

test("extractVoiceCaseDraft calls existing extract endpoint and preserves ApiError.caseData", async () => {
  const originalFetch = globalThis.fetch;
  const captured: Array<{ url: string; method: string; body: string }> = [];

  try {
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      captured.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: String(init?.body ?? ""),
      });
      if (captured.length === 1) {
        return new Response(
          JSON.stringify({
            draft: {
              id: "d1",
              extraction_status: "completed",
              llm_model: "gemini-3.6-flash",
              source_transcript_layer: "worker_edited_transcript",
            },
            case: { id: "c1", status: "draft", extraction_drafts: [{ id: "d1" }] },
          }),
          { status: 200 }
        );
      }
      return new Response(
        JSON.stringify({
          error_code: "rate_limited",
          detail: "Gemini quota or rate limit reached (HTTP 429).",
          retryable: true,
          case: { id: "c1", status: "draft", extraction_drafts: [{ id: "d1" }, { id: "d2" }] },
        }),
        { status: 429 }
      );
    }) as typeof fetch;

    const res = await extractVoiceCaseDraft("c1", "r1", {
      consent_granted: true,
      transcript_approved_by_worker: true,
      transcript_layer: "worker_edited_transcript",
    });
    assert.equal(captured[0].url, "/api/cases/c1/voice/r1/extract");
    assert.equal(captured[0].method, "POST");
    assert.deepEqual(JSON.parse(captured[0].body), {
      consent_granted: true,
      transcript_approved_by_worker: true,
      transcript_layer: "worker_edited_transcript",
    });
    assert.equal(res.draft.llm_model, "gemini-3.6-flash");

    await assert.rejects(
      () =>
        extractVoiceCaseDraft("c1", "r1", {
          consent_granted: true,
          transcript_approved_by_worker: true,
          transcript_layer: "raw_transcript",
        }),
      (err: unknown) => {
        assert(err instanceof ApiError);
        assert.equal(err.status, 429);
        assert.equal(err.errorCode, "rate_limited");
        assert.equal(err.caseData?.extraction_drafts.length, 2);
        return true;
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("extraction error mapping covers Gemini keyless, auth, rate limit, timeout, and schema errors", () => {
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "k", status: 503, errorCode: "key_not_set" }), "extract"),
    /Gemini API key is not configured/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "a", status: 502, errorCode: "provider_auth_error" }), "extract"),
    /Gemini authentication failed/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "r", status: 429, errorCode: "rate_limited" }), "extract"),
    /Gemini.*429/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "t", status: 504, errorCode: "provider_timeout" }), "extract"),
    /Gemini timed out/i
  );
  assert.match(
    formatVoiceErrorMessage(new ApiError({ message: "", status: 502, errorCode: "schema_violation" }), "extract"),
    /structured response/i
  );
});

test("worker edits update provenance to worker_edited while conflict resolution preserves recording provenance", () => {
  const initialProv: Record<string, FieldProvenanceEntry> = {
    platform: {
      origin: "llm_extracted",
      source_quote: "Swiggy",
      voice_recording_id: "rec-1",
      transcript_layer: "worker_edited_transcript",
      value: "swiggy",
    },
    discrepancy_amount_inr: {
      origin: "llm_extracted",
      source_quote: "850 rupees",
      voice_recording_id: "rec-1",
      transcript_layer: "worker_edited_transcript",
      value: 850,
    },
  };

  const editedProv = markFieldAsWorkerEdited(initialProv, "discrepancy_amount_inr");
  assert.equal(editedProv.discrepancy_amount_inr.origin, "worker_edited");
  assert.equal(editedProv.discrepancy_amount_inr.source_quote, null);
  // Untouched AI field preserves original quote provenance
  assert.equal(editedProv.platform.origin, "llm_extracted");
  assert.equal(editedProv.platform.source_quote, "Swiggy");

  const sanitized = sanitizeProvenanceMap(editedProv);
  assert.ok(sanitized);
  assert.equal("value" in sanitized.platform, false);

  const details: PaymentDiscrepancyDetails = {
    platform: "swiggy",
    work_city: null,
    payout_cycle_raw: "this week",
    payout_cycle_start: null,
    payout_cycle_end: null,
    incentive_scheme_name: null,
    expected_amount_inr: null,
    received_amount_inr: null,
    discrepancy_amount_inr: 850,
    currency: "rupees",
    work_completed_summary: null,
    affected_order_or_trip_ids: null,
    prior_ticket_id: null,
    support_contact_summary: null,
    support_response_summary: null,
    issue_description: "Short payout",
  };

  const resolved = resolveConflictIntoDetails({
    details,
    provenance: editedProv,
    conflict: {
      field: "discrepancy_amount_inr",
      existing_value: 850,
      existing_provenance: initialProv.discrepancy_amount_inr,
      proposed_value: 1200,
      proposed_provenance: {
        origin: "llm_extracted",
        source_quote: "1200 rupees",
        voice_recording_id: "rec-2",
        transcript_layer: "worker_edited_transcript",
      },
    },
  });
  assert.equal(resolved.updatedDetails.discrepancy_amount_inr, 1200);
  assert.equal(resolved.updatedProvenance.discrepancy_amount_inr.voice_recording_id, "rec-2");
  assert.equal(resolved.updatedProvenance.discrepancy_amount_inr.source_quote, "1200 rupees");
});

test("rejected claims, unresolved conflicts, and missing/followup grouping behave accurately", () => {
  const recordings = [
    { id: "rec-1", recording_order: 1 },
    { id: "rec-2", recording_order: 2 },
  ] as VoiceRecording[];

  const drafts: ExtractionDraft[] = [
    {
      id: "d1",
      case_id: "c1",
      voice_recording_id: "rec-1",
      source_transcript_layer: "worker_edited_transcript",
      source_transcript_snapshot: "Swiggy 850 rupees",
      llm_provider: "gemini",
      llm_model: "gemini-3.6-flash",
      extraction_status: "completed",
      gemini_consent_granted: true,
      gemini_consented_at: "2026-11-28T00:00:00Z",
      extracted_fields: { platform: "swiggy", discrepancy_amount_inr: 850 },
      field_provenance: null,
      stripped_ungrounded_fields: {
        expected_amount_inr: {
          rejected_value: 5000,
          rejected_quote: "850 rupees",
          reason: "Numeric mismatch between value and quote.",
        },
      },
      conflict_suggestions: null,
      missing_fields: [],
      error_message: null,
      created_at: "2026-11-28T00:01:00Z",
    },
    {
      id: "d2",
      case_id: "c1",
      voice_recording_id: "rec-2",
      source_transcript_layer: "worker_edited_transcript",
      source_transcript_snapshot: "Actually 1200 rupees",
      llm_provider: "gemini",
      llm_model: "gemini-3.6-flash",
      extraction_status: "completed",
      gemini_consent_granted: true,
      gemini_consented_at: "2026-11-28T00:02:00Z",
      extracted_fields: { discrepancy_amount_inr: 1200 },
      field_provenance: null,
      stripped_ungrounded_fields: null,
      conflict_suggestions: {
        discrepancy_amount_inr: {
          field: "discrepancy_amount_inr",
          existing_value: 850,
          existing_provenance: null,
          proposed_value: 1200,
          proposed_provenance: null,
        },
      },
      missing_fields: [],
      error_message: null,
      created_at: "2026-11-28T00:02:00Z",
    },
  ];

  const rejected = collectLatestRejectedClaims(drafts, recordings);
  assert.equal(rejected.expected_amount_inr.claim.rejected_value, 5000);
  assert.equal(rejected.expected_amount_inr.recordingOrder, 1);

  const unresolvedBefore = collectUnresolvedConflicts(
    drafts,
    { discrepancy_amount_inr: 850 } as PaymentDiscrepancyDetails,
    recordings
  );
  assert.equal(unresolvedBefore.length, 1);
  assert.equal(unresolvedBefore[0].proposedRecordingOrder, 2);

  // Once worker applies 1200 into active draft details, conflict badge clears while drafts history remains
  const unresolvedAfter = collectUnresolvedConflicts(
    drafts,
    { discrepancy_amount_inr: 1200 } as PaymentDiscrepancyDetails,
    recordings
  );
  assert.equal(unresolvedAfter.length, 0);

  const grouped = groupMissingAndFollowupItems([
    { field: "expected_amount_inr", importance: "clarification_required", prompt: "Clarify expected amount" },
    { field: "work_city", importance: "helpful", prompt: "Which city?" },
    { field: "followup_question", importance: "followup", prompt: "Did you get a ticket ID?" },
  ]);
  assert.equal(grouped.clarifications.length, 1);
  assert.equal(grouped.missingFacts.length, 1);
  assert.equal(grouped.followupQuestions.length, 1);
});
