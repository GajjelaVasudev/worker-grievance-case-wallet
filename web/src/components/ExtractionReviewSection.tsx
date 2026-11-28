import type {
  CapabilitiesResponse,
  ConflictSuggestion,
  FieldProvenanceEntry,
  GrievanceCase,
} from "../api/client";
import {
  collectLatestRejectedClaims,
  collectUnresolvedConflicts,
  groupMissingAndFollowupItems,
  recordingOrderMap,
} from "../voice/extractionHelpers";

interface ExtractionReviewSectionProps {
  capabilities: CapabilitiesResponse | null;
  selectedCase: GrievanceCase;
  dismissedConflictKeys: Record<string, boolean>;
  onDismissConflict: (key: string) => void;
  onApplyConflict: (conflict: ConflictSuggestion) => void;
}

function formatProvenanceSource(
  prov: FieldProvenanceEntry | null,
  orderById: Map<string, number>
): string {
  if (!prov) return "Initial / manual value";
  const recOrder = prov.voice_recording_id ? orderById.get(prov.voice_recording_id) : undefined;
  const quotePart = prov.source_quote ? `quote: "${prov.source_quote}"` : prov.origin;
  return recOrder ? `Recording #${recOrder} (${quotePart})` : quotePart;
}

export function ExtractionReviewSection({
  capabilities,
  selectedCase,
  dismissedConflictKeys,
  onDismissConflict,
  onApplyConflict,
}: ExtractionReviewSectionProps) {
  const llmConfigured = Boolean(capabilities?.llm.configured);
  const orderById = recordingOrderMap(selectedCase.voice_recordings);
  const activeDetails = selectedCase.confirmed_details ?? selectedCase.draft_details;

  const rejectedClaims = Object.values(
    collectLatestRejectedClaims(selectedCase.extraction_drafts ?? [], selectedCase.voice_recordings ?? [])
  );
  const unresolvedConflicts = collectUnresolvedConflicts(
    selectedCase.extraction_drafts ?? [],
    activeDetails,
    selectedCase.voice_recordings ?? []
  ).filter((item) => !dismissedConflictKeys[`${item.draftId}:${item.field}`]);

  const { clarifications, missingFacts, followupQuestions } = groupMissingAndFollowupItems(
    selectedCase.missing_fields ?? []
  );

  const drafts = selectedCase.extraction_drafts ?? [];

  return (
    <div className="space-y-3">
      {capabilities && !llmConfigured && (
        <div
          role="status"
          className="rounded border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900"
        >
          <strong>Gemini AI extraction unavailable ({capabilities.llm.status_message}):</strong>{" "}
          Structured fields are never fabricated or filled with sample data. Review and enter all
          case details manually below before confirming.
        </div>
      )}

      {unresolvedConflicts.length > 0 && (
        <div
          data-testid="conflict-suggestions-panel"
          className="rounded border border-orange-300 bg-orange-50 p-3 space-y-2.5 text-xs text-orange-950"
        >
          <div className="font-semibold">
            Conflicting Values Across Recordings ({unresolvedConflicts.length}) &mdash; Worker Review Required
          </div>
          <p className="text-[11px] text-orange-900">
            {selectedCase.status === "draft"
              ? "Previous values were NOT overwritten automatically. Choose which value to keep in your draft."
              : "This case is already confirmed; confirmed details remain locked. Use 'Amend Confirmed Case' below with a reason if you want to update confirmed facts."}
          </p>
          <ul className="space-y-2">
            {unresolvedConflicts.map((item) => {
              const key = `${item.draftId}:${item.field}`;
              return (
                <li key={key} className="rounded border border-orange-200 bg-white p-2.5 space-y-1.5">
                  <div className="font-semibold text-slate-900">
                    Field: <code>{item.field}</code>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div className="rounded bg-slate-50 p-2 border border-slate-200">
                      <div className="font-medium text-slate-800">
                        Current value: <code>{String(item.conflict.existing_value)}</code>
                      </div>
                      <div className="text-[11px] text-slate-600">
                        Source: {formatProvenanceSource(item.conflict.existing_provenance, orderById)}
                      </div>
                    </div>
                    <div className="rounded bg-amber-50 p-2 border border-amber-200">
                      <div className="font-medium text-amber-950">
                        New extraction{item.proposedRecordingOrder ? ` (#${item.proposedRecordingOrder})` : ""}:{" "}
                        <code>{String(item.conflict.proposed_value)}</code>
                      </div>
                      <div className="text-[11px] text-amber-900">
                        Source: {formatProvenanceSource(item.conflict.proposed_provenance, orderById)}
                      </div>
                    </div>
                  </div>
                  {selectedCase.status === "draft" && (
                    <div className="flex flex-wrap gap-2 pt-1">
                      <button
                        type="button"
                        onClick={() => onApplyConflict(item.conflict)}
                        className="rounded bg-orange-700 hover:bg-orange-800 px-2.5 py-1 text-[11px] font-medium text-white"
                      >
                        Use Proposed Value ({String(item.conflict.proposed_value)})
                      </button>
                      <button
                        type="button"
                        onClick={() => onDismissConflict(key)}
                        className="rounded border border-slate-300 bg-white px-2.5 py-1 text-[11px] text-slate-800"
                      >
                        Keep Current Value
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {rejectedClaims.length > 0 && (
        <div
          data-testid="rejected-claims-panel"
          className="rounded border border-red-300 bg-red-50 p-2.5 text-xs text-red-950 space-y-1"
        >
          <div className="font-semibold">
            Claims Rejected by Grounding Validation ({rejectedClaims.length})
          </div>
          <p className="text-[11px] text-red-900">
            Unsupported or mismatched AI outputs were automatically stripped and left empty until verified by you:
          </p>
          <ul className="list-disc pl-4 space-y-0.5 text-[11px]">
            {rejectedClaims.map((rc) => (
              <li key={rc.field}>
                <code>{rc.field}</code>
                {rc.recordingOrder ? ` (Recording #${rc.recordingOrder})` : ""}: {rc.claim.reason}
                {rc.claim.rejected_value !== null && rc.claim.rejected_value !== undefined
                  ? ` — rejected "${String(rc.claim.rejected_value)}"`
                  : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(clarifications.length > 0 || missingFacts.length > 0 || followupQuestions.length > 0) && (
        <div className="rounded border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-950 space-y-2">
          {clarifications.length > 0 && (
            <div>
              <div className="font-semibold">Clarification Required ({clarifications.length}):</div>
              <ul className="list-disc pl-4 mt-0.5 space-y-0.5">
                {clarifications.map((m, idx) => (
                  <li key={`${m.field}-${idx}`}>
                    <code>{m.field}</code>: {m.prompt}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {missingFacts.length > 0 && (
            <div>
              <div className="font-semibold">Missing / Unknown Information ({missingFacts.length}):</div>
              <ul className="list-disc pl-4 mt-0.5 space-y-0.5">
                {missingFacts.map((m, idx) => (
                  <li key={`${m.field}-${idx}`}>
                    <code>{m.field}</code> ({m.importance}): {m.prompt}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {followupQuestions.length > 0 && (
            <div>
              <div className="font-semibold">Suggested Follow-up Questions ({followupQuestions.length}):</div>
              <ul className="list-disc pl-4 mt-0.5 space-y-0.5">
                {followupQuestions.map((q, idx) => (
                  <li key={`followup-${idx}`}>{q.prompt}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {drafts.length > 0 && (
        <details className="rounded border border-slate-200 bg-slate-50 p-2.5 text-xs">
          <summary className="cursor-pointer font-semibold text-slate-800">
            Extraction History ({drafts.length} {drafts.length === 1 ? "run" : "runs"} preserved)
          </summary>
          <ul className="mt-2 space-y-1.5">
            {drafts.map((d, index) => {
              const recOrder = orderById.get(d.voice_recording_id);
              const groundedCount = Object.keys(d.field_provenance ?? {}).length;
              const rejectedCount = Object.keys(d.stripped_ungrounded_fields ?? {}).length;
              const conflictCount = Object.keys(d.conflict_suggestions ?? {}).length;
              return (
                <li key={d.id} className="rounded border border-slate-200 bg-white p-2 text-[11px] space-y-0.5">
                  <div className="font-medium flex flex-wrap justify-between gap-2">
                    <span>
                      Run #{index + 1} &middot; {recOrder ? `Recording #${recOrder}` : "Recording"} ({d.source_transcript_layer})
                    </span>
                    <span className="uppercase">{d.extraction_status}</span>
                  </div>
                  <div className="text-slate-600">
                    Model: {d.llm_model ?? d.llm_provider} &middot; Grounded: {groundedCount} &middot; Rejected:{" "}
                    {rejectedCount} &middot; Conflicts: {conflictCount} &middot;{" "}
                    {new Date(d.created_at).toLocaleString()}
                  </div>
                  {d.error_message && <div className="text-red-800">{d.error_message}</div>}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </div>
  );
}
