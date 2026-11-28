import type { FieldProvenanceEntry } from "../api/client";
import type { RejectedClaimSummary } from "../voice/extractionHelpers";

interface FieldProvenanceHintProps {
  fieldName: string;
  isEmpty: boolean;
  provenance?: FieldProvenanceEntry;
  rejectedClaim?: RejectedClaimSummary;
  recordingOrderById: Map<string, number>;
}

export function FieldProvenanceHint({
  fieldName,
  isEmpty,
  provenance,
  rejectedClaim,
  recordingOrderById,
}: FieldProvenanceHintProps) {
  if (isEmpty && rejectedClaim) {
    return (
      <div
        data-testid={`provenance-rejected-${fieldName}`}
        className="mt-1 rounded border border-red-200 bg-red-50 px-2 py-1 text-[11px] text-red-900"
      >
        <span className="font-semibold">Rejected by grounding check:</span> {rejectedClaim.claim.reason}
        {rejectedClaim.claim.rejected_value !== null && rejectedClaim.claim.rejected_value !== undefined && (
          <span className="block text-red-800">
            Rejected AI value: <code>{String(rejectedClaim.claim.rejected_value)}</code>
            {rejectedClaim.claim.rejected_quote ? ` (quote: "${rejectedClaim.claim.rejected_quote}")` : ""}
          </span>
        )}
      </div>
    );
  }

  if (provenance?.origin === "llm_extracted" && provenance.source_quote) {
    const recOrder = provenance.voice_recording_id
      ? recordingOrderById.get(provenance.voice_recording_id)
      : undefined;
    const layerText =
      provenance.transcript_layer === "raw_transcript"
        ? "original ASR transcript"
        : provenance.transcript_layer === "worker_edited_transcript"
        ? "worker-edited transcript"
        : "transcript";
    return (
      <div
        data-testid={`provenance-supported-${fieldName}`}
        className="mt-1 rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-950"
      >
        <span className="font-semibold">Supported by transcript:</span> &ldquo;{provenance.source_quote}&rdquo;
        <span className="text-emerald-800">
          {" "}
          ({recOrder ? `Recording #${recOrder}, ` : ""}
          {layerText})
        </span>
      </div>
    );
  }

  if (provenance?.origin === "worker_edited" || provenance?.origin === "worker_input") {
    return (
      <div
        data-testid={`provenance-worker-${fieldName}`}
        className="mt-1 rounded border border-blue-200 bg-blue-50 px-2 py-1 text-[11px] text-blue-900"
      >
        <span className="font-semibold">Worker corrected / entered</span> (human review supersedes AI draft)
      </div>
    );
  }

  if (provenance?.origin === "transcript_quote" && provenance.source_quote) {
    return (
      <div className="mt-1 rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-950">
        <span className="font-semibold">Transcript quote:</span> &ldquo;{provenance.source_quote}&rdquo;
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="mt-1 text-[11px] text-amber-800">
        Needs clarification (currently unknown / empty &mdash; not inferred)
      </div>
    );
  }

  return null;
}
