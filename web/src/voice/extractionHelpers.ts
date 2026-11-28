import type {
  ConflictSuggestion,
  ExtractionDraft,
  FieldProvenanceEntry,
  MissingFieldItem,
  PaymentDiscrepancyDetails,
  PlatformName,
  StrippedClaimEntry,
  VoiceRecording,
} from "../api/client.ts";

export interface RejectedClaimSummary {
  field: string;
  claim: StrippedClaimEntry;
  draftCreatedAt: string;
  recordingOrder: number | null;
}

export interface ConflictItemSummary {
  draftId: string;
  field: string;
  conflict: ConflictSuggestion;
  proposedRecordingOrder: number | null;
}

export function sanitizeProvenanceMap(
  prov: Record<string, FieldProvenanceEntry> | null | undefined
): Record<string, FieldProvenanceEntry> | null {
  if (!prov) return null;
  const cleaned: Record<string, FieldProvenanceEntry> = {};
  for (const [key, entry] of Object.entries(prov)) {
    if (!entry || !entry.origin) continue;
    cleaned[key] = {
      origin: entry.origin,
      source_quote: entry.source_quote ?? null,
      voice_recording_id: entry.voice_recording_id ?? null,
      transcript_layer: entry.transcript_layer ?? null,
    };
  }
  return Object.keys(cleaned).length > 0 ? cleaned : null;
}

export function markFieldAsWorkerEdited(
  provenance: Record<string, FieldProvenanceEntry> | null | undefined,
  fieldName: string
): Record<string, FieldProvenanceEntry> {
  const next: Record<string, FieldProvenanceEntry> = { ...(provenance ?? {}) };
  next[fieldName] = {
    origin: "worker_edited",
    source_quote: null,
    voice_recording_id: null,
    transcript_layer: null,
  };
  return next;
}

export function resolveConflictIntoDetails(params: {
  details: PaymentDiscrepancyDetails;
  provenance: Record<string, FieldProvenanceEntry> | null | undefined;
  conflict: ConflictSuggestion;
}): {
  updatedDetails: PaymentDiscrepancyDetails;
  updatedPlatform: PlatformName | null;
  updatedProvenance: Record<string, FieldProvenanceEntry>;
} {
  const updatedDetails: PaymentDiscrepancyDetails = { ...params.details };
  const field = params.conflict.field as keyof PaymentDiscrepancyDetails;
  (updatedDetails as unknown as Record<string, unknown>)[field] = params.conflict.proposed_value;

  const updatedProvenance: Record<string, FieldProvenanceEntry> = {
    ...(params.provenance ?? {}),
  };
  if (params.conflict.proposed_provenance) {
    updatedProvenance[params.conflict.field] = {
      origin: params.conflict.proposed_provenance.origin,
      source_quote: params.conflict.proposed_provenance.source_quote ?? null,
      voice_recording_id: params.conflict.proposed_provenance.voice_recording_id ?? null,
      transcript_layer: params.conflict.proposed_provenance.transcript_layer ?? null,
    };
  } else {
    updatedProvenance[params.conflict.field] = {
      origin: "worker_edited",
      source_quote: null,
      voice_recording_id: null,
      transcript_layer: null,
    };
  }

  const updatedPlatform =
    params.conflict.field === "platform" && typeof params.conflict.proposed_value === "string"
      ? (params.conflict.proposed_value as PlatformName)
      : updatedDetails.platform;

  return { updatedDetails, updatedPlatform, updatedProvenance };
}

export function recordingOrderMap(recordings: VoiceRecording[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const rec of recordings) {
    map.set(rec.id, rec.recording_order);
  }
  return map;
}

export function collectLatestRejectedClaims(
  drafts: ExtractionDraft[],
  recordings: VoiceRecording[]
): Record<string, RejectedClaimSummary> {
  const orderById = recordingOrderMap(recordings);
  const result: Record<string, RejectedClaimSummary> = {};
  for (let i = drafts.length - 1; i >= 0; i -= 1) {
    const draft = drafts[i];
    const stripped = draft.stripped_ungrounded_fields;
    if (!stripped) continue;
    for (const [field, claim] of Object.entries(stripped)) {
      if (result[field]) continue;
      result[field] = {
        field,
        claim,
        draftCreatedAt: draft.created_at,
        recordingOrder: orderById.get(draft.voice_recording_id) ?? null,
      };
    }
  }
  return result;
}

export function collectUnresolvedConflicts(
  drafts: ExtractionDraft[],
  activeDetails: PaymentDiscrepancyDetails | null,
  recordings: VoiceRecording[]
): ConflictItemSummary[] {
  const orderById = recordingOrderMap(recordings);
  const activeMap = (activeDetails ?? {}) as unknown as Record<string, unknown>;
  const items: ConflictItemSummary[] = [];

  for (let i = drafts.length - 1; i >= 0; i -= 1) {
    const draft = drafts[i];
    if (!draft.conflict_suggestions) continue;
    for (const [field, conflict] of Object.entries(draft.conflict_suggestions)) {
      // Only show if proposed value still differs from active value (not yet applied)
      const currentVal = activeMap[field];
      if (currentVal !== undefined && currentVal === conflict.proposed_value) {
        continue;
      }
      items.push({
        draftId: draft.id,
        field,
        conflict,
        proposedRecordingOrder: orderById.get(draft.voice_recording_id) ?? null,
      });
    }
  }
  return items;
}

export function groupMissingAndFollowupItems(items: MissingFieldItem[]): {
  clarifications: MissingFieldItem[];
  missingFacts: MissingFieldItem[];
  followupQuestions: MissingFieldItem[];
} {
  const clarifications: MissingFieldItem[] = [];
  const missingFacts: MissingFieldItem[] = [];
  const followupQuestions: MissingFieldItem[] = [];

  for (const item of items) {
    if (item.importance === "clarification_required") {
      clarifications.push(item);
    } else if (item.importance === "followup") {
      followupQuestions.push(item);
    } else {
      missingFacts.push(item);
    }
  }
  return { clarifications, missingFacts, followupQuestions };
}
