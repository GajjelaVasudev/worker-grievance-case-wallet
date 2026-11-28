import { FormEvent, useEffect, useState } from "react";
import {
  amendConfirmedCase,
  appendMilestone,
  CapabilitiesResponse,
  confirmCase,
  createPaymentCase,
  deleteCase,
  deleteEvidenceFile,
  EvidenceCategory,
  fetchCapabilities,
  fetchHealth,
  GrievanceCase,
  HealthResponse,
  listCases,
  PaymentDiscrepancyDetails,
  PlatformName,
  updateDraftCase,
  uploadEvidenceFile,
} from "./api/client";
import { VoiceRecorderPanel } from "./components/VoiceRecorderPanel";

function emptyDetails(platform: PlatformName): PaymentDiscrepancyDetails {
  return {
    platform,
    work_city: null,
    payout_cycle_raw: null,
    payout_cycle_start: null,
    payout_cycle_end: null,
    incentive_scheme_name: null,
    expected_amount_inr: null,
    received_amount_inr: null,
    discrepancy_amount_inr: null,
    affected_order_or_trip_ids: null,
    prior_ticket_id: null,
    issue_description: null,
  };
}

function toNullableNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [cases, setCases] = useState<GrievanceCase[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // New Draft Form
  const [newPlatform, setNewPlatform] = useState<PlatformName>("swiggy");
  const [newTitle, setNewTitle] = useState("");
  const [newCity, setNewCity] = useState("");
  const [newCycleRaw, setNewCycleRaw] = useState("");
  const [newExpected, setNewExpected] = useState("");
  const [newReceived, setNewReceived] = useState("");
  const [newScheme, setNewScheme] = useState("");
  const [newDescription, setNewDescription] = useState("");

  // Selected Case Edit / Confirm / Amend State
  const [editPlatform, setEditPlatform] = useState<PlatformName>("swiggy");
  const [editJurisdiction, setEditJurisdiction] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editCity, setEditCity] = useState("");
  const [editCycleRaw, setEditCycleRaw] = useState("");
  const [editScheme, setEditScheme] = useState("");
  const [editExpected, setEditExpected] = useState("");
  const [editReceived, setEditReceived] = useState("");
  const [editDiscrepancy, setEditDiscrepancy] = useState("");
  const [editTicketId, setEditTicketId] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [confirmNote, setConfirmNote] = useState("");
  const [amendReason, setAmendReason] = useState("");

  // Evidence Upload State
  const [evidenceCategory, setEvidenceCategory] = useState<EvidenceCategory>("payout_summary");
  const [evidenceNote, setEvidenceNote] = useState("");
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);

  // Timeline Milestone State
  const [milestoneType, setMilestoneType] = useState<
    | "worker_reported_submission"
    | "evidence_backed_submission"
    | "platform_acknowledgement_reported"
    | "platform_response_reported"
    | "escalation_reported"
    | "closed"
    | "note_added"
  >("worker_reported_submission");
  const [milestoneTitle, setMilestoneTitle] = useState("");
  const [milestoneRef, setMilestoneRef] = useState("");
  const [milestoneNotes, setMilestoneNotes] = useState("");
  const [milestoneEvidenceIds, setMilestoneEvidenceIds] = useState<string[]>([]);

  const selectedCase = cases.find((item) => item.id === selectedCaseId) ?? cases[0] ?? null;

  useEffect(() => {
    Promise.all([fetchHealth(), fetchCapabilities(), listCases()])
      .then(([healthData, capData, caseList]) => {
        setHealth(healthData);
        setCapabilities(capData);
        setCases(caseList);
        if (caseList.length > 0) {
          setSelectedCaseId(caseList[0].id);
        }
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to initialize application");
      });
  }, []);

  useEffect(() => {
    setMilestoneEvidenceIds([]);
  }, [selectedCase?.id]);

  useEffect(() => {
    if (!selectedCase) return;
    const active = selectedCase.confirmed_details ?? selectedCase.draft_details ?? emptyDetails(selectedCase.platform);
    setEditPlatform(selectedCase.platform);
    setEditJurisdiction(selectedCase.jurisdiction_state ?? "");
    setEditTitle(selectedCase.summary_title ?? "");
    setEditCity(active.work_city ?? "");
    setEditCycleRaw(active.payout_cycle_raw ?? "");
    setEditScheme(active.incentive_scheme_name ?? "");
    setEditExpected(active.expected_amount_inr !== null ? String(active.expected_amount_inr) : "");
    setEditReceived(active.received_amount_inr !== null ? String(active.received_amount_inr) : "");
    setEditDiscrepancy(active.discrepancy_amount_inr !== null ? String(active.discrepancy_amount_inr) : "");
    setEditTicketId(active.prior_ticket_id ?? "");
    setEditDescription(active.issue_description ?? "");
  }, [selectedCase?.id, selectedCase?.updated_at]);

  function replaceCaseInList(updated: GrievanceCase) {
    setCases((prev) => {
      const exists = prev.some((c) => c.id === updated.id);
      if (!exists) return [updated, ...prev];
      return prev.map((c) => (c.id === updated.id ? updated : c));
    });
    setSelectedCaseId(updated.id);
  }

  function buildEditedDetails(): PaymentDiscrepancyDetails {
    return {
      platform: editPlatform,
      work_city: editCity.trim() || null,
      payout_cycle_raw: editCycleRaw.trim() || null,
      payout_cycle_start: null,
      payout_cycle_end: null,
      incentive_scheme_name: editScheme.trim() || null,
      expected_amount_inr: toNullableNumber(editExpected),
      received_amount_inr: toNullableNumber(editReceived),
      discrepancy_amount_inr: toNullableNumber(editDiscrepancy),
      affected_order_or_trip_ids: null,
      prior_ticket_id: editTicketId.trim() || null,
      issue_description: editDescription.trim() || null,
    };
  }

  async function handleEnsureVoiceDraftCase(): Promise<GrievanceCase> {
    if (selectedCase) return selectedCase;
    const created = await createPaymentCase({
      platform: "other",
      jurisdiction_state: null,
      summary_title: null,
      details: {
        ...emptyDetails("other"),
        platform: null,
      },
    });
    replaceCaseInList(created);
    return created;
  }

  async function handleCreateDraft(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setStatusMessage(null);
    try {
      const created = await createPaymentCase({
        platform: newPlatform,
        jurisdiction_state: null,
        summary_title: newTitle.trim() || null,
        details: {
          ...emptyDetails(newPlatform),
          work_city: newCity.trim() || null,
          payout_cycle_raw: newCycleRaw.trim() || null,
          incentive_scheme_name: newScheme.trim() || null,
          expected_amount_inr: toNullableNumber(newExpected),
          received_amount_inr: toNullableNumber(newReceived),
          issue_description: newDescription.trim() || null,
        },
      });
      replaceCaseInList(created);
      setNewTitle("");
      setNewCity("");
      setNewCycleRaw("");
      setNewExpected("");
      setNewReceived("");
      setNewScheme("");
      setNewDescription("");
      setStatusMessage(`Created draft ${created.case_code}. Review and confirm to lock facts.`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create draft case");
    }
  }

  async function handleSaveDraft() {
    if (!selectedCase) return;
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await updateDraftCase(selectedCase.id, {
        platform: editPlatform,
        jurisdiction_state: editJurisdiction.trim() || null,
        summary_title: editTitle.trim() || null,
        details: buildEditedDetails(),
      });
      replaceCaseInList(updated);
      setStatusMessage(`Draft ${updated.case_code} saved.`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update draft");
    }
  }

  async function handleConfirmCase() {
    if (!selectedCase) return;
    setError(null);
    setStatusMessage(null);
    try {
      const confirmed = await confirmCase(selectedCase.id, {
        confirm: true,
        platform: editPlatform,
        jurisdiction_state: editJurisdiction.trim() || null,
        summary_title: editTitle.trim() || null,
        details: buildEditedDetails(),
        worker_confirmation_note: confirmNote.trim() || null,
      });
      replaceCaseInList(confirmed);
      setConfirmNote("");
      setStatusMessage(`Case ${confirmed.case_code} confirmed by worker.`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to confirm case");
    }
  }

  async function handleAmendConfirmed() {
    if (!selectedCase) return;
    setError(null);
    setStatusMessage(null);
    try {
      const amended = await amendConfirmedCase(selectedCase.id, {
        platform: editPlatform,
        jurisdiction_state: editJurisdiction.trim() || null,
        summary_title: editTitle.trim() || null,
        details: buildEditedDetails(),
        amendment_reason: amendReason.trim(),
      });
      replaceCaseInList(amended);
      setAmendReason("");
      setStatusMessage(`Amended ${amended.case_code} and recorded timeline audit event.`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to amend confirmed case");
    }
  }

  async function handleUploadEvidence(e: FormEvent) {
    e.preventDefault();
    if (!selectedCase || !evidenceFile) return;
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await uploadEvidenceFile(
        selectedCase.id,
        evidenceCategory,
        evidenceFile,
        evidenceNote
      );
      replaceCaseInList(updated);
      setEvidenceFile(null);
      setEvidenceNote("");
      setStatusMessage("Evidence uploaded and checklist refreshed.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Evidence upload failed");
    }
  }

  async function handleDeleteEvidence(evidenceId: string) {
    if (!selectedCase) return;
    setError(null);
    try {
      const updated = await deleteEvidenceFile(selectedCase.id, evidenceId);
      replaceCaseInList(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete evidence");
    }
  }

  async function handleAppendMilestone(e: FormEvent) {
    e.preventDefault();
    if (!selectedCase) return;
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await appendMilestone(selectedCase.id, {
        event_type: milestoneType,
        title: milestoneTitle.trim(),
        notes: milestoneNotes.trim() || null,
        external_reference_id: milestoneRef.trim() || null,
        linked_evidence_ids: milestoneEvidenceIds.length > 0 ? milestoneEvidenceIds : null,
      });
      replaceCaseInList(updated);
      setMilestoneTitle("");
      setMilestoneRef("");
      setMilestoneNotes("");
      setMilestoneEvidenceIds([]);
      setStatusMessage("Timeline event appended.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add timeline milestone");
    }
  }

  function toggleMilestoneEvidence(evidenceId: string) {
    setMilestoneEvidenceIds((prev) =>
      prev.includes(evidenceId) ? prev.filter((id) => id !== evidenceId) : [...prev, evidenceId]
    );
  }

  async function handleDeleteCase(caseId: string) {
    setError(null);
    setStatusMessage(null);
    try {
      await deleteCase(caseId);
      const remaining = cases.filter((c) => c.id !== caseId);
      setCases(remaining);
      setSelectedCaseId(remaining[0]?.id ?? null);
      setStatusMessage("Case and stored evidence deleted.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete case");
    }
  }

  return (
    <main className="mx-auto max-w-5xl p-4 sm:p-6 space-y-6">
      <header className="border-b border-slate-300 pb-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {capabilities?.app_name ?? "Worker Grievance Case Wallet"}
          </h1>
          <p className="text-sm text-slate-600">
            Phase 3: Voice-First Grievance Recording, Sarvam Transcription &amp; Evidence Wallet
          </p>
        </div>
        <div className="text-xs text-slate-700 space-y-1 text-right">
          <div>
            Health: {health ? `${health.status} (DB: ${health.database}, Storage: ${health.storage})` : "Checking..."}
          </div>
          <div>
            ASR: {capabilities?.asr.status_code ?? "..."} | LLM: {capabilities?.llm.status_code ?? "..."}
          </div>
        </div>
      </header>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      )}
      {statusMessage && (
        <div className="rounded border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900">
          {statusMessage}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section className="space-y-4">
          <form onSubmit={handleCreateDraft} className="rounded border border-slate-300 bg-white p-4 space-y-3">
            <h2 className="font-semibold">New Payment Discrepancy (Draft)</h2>
            <p className="text-xs text-slate-600">Leave unknown fields blank — they remain null.</p>
            <label className="block text-xs font-medium">
              Platform
              <select
                value={newPlatform}
                onChange={(e) => setNewPlatform(e.target.value as PlatformName)}
                className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
              >
                <option value="swiggy">Swiggy</option>
                <option value="zomato">Zomato</option>
                <option value="rapido">Rapido</option>
                <option value="uber">Uber</option>
                <option value="other">Other</option>
              </select>
            </label>
            <input
              type="text"
              placeholder="Summary title (optional)"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              className="w-full rounded border border-slate-300 p-1.5 text-sm"
            />
            <div className="grid grid-cols-2 gap-2">
              <input
                type="text"
                placeholder="City (optional)"
                value={newCity}
                onChange={(e) => setNewCity(e.target.value)}
                className="rounded border border-slate-300 p-1.5 text-sm"
              />
              <input
                type="text"
                placeholder="Payout cycle"
                value={newCycleRaw}
                onChange={(e) => setNewCycleRaw(e.target.value)}
                className="rounded border border-slate-300 p-1.5 text-sm"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input
                type="number"
                min="0"
                step="any"
                placeholder="Expected INR"
                value={newExpected}
                onChange={(e) => setNewExpected(e.target.value)}
                className="rounded border border-slate-300 p-1.5 text-sm"
              />
              <input
                type="number"
                min="0"
                step="any"
                placeholder="Received INR"
                value={newReceived}
                onChange={(e) => setNewReceived(e.target.value)}
                className="rounded border border-slate-300 p-1.5 text-sm"
              />
            </div>
            <input
              type="text"
              placeholder="Incentive scheme name (optional)"
              value={newScheme}
              onChange={(e) => setNewScheme(e.target.value)}
              className="w-full rounded border border-slate-300 p-1.5 text-sm"
            />
            <textarea
              placeholder="Describe the payment / incentive issue"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              rows={2}
              className="w-full rounded border border-slate-300 p-1.5 text-sm"
            />
            <button
              type="submit"
              className="w-full rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white"
            >
              Create Draft Case
            </button>
          </form>

          <div className="rounded border border-slate-300 bg-white p-4 space-y-2">
            <h2 className="font-semibold">Local Wallet Cases ({cases.length})</h2>
            {cases.length === 0 ? (
              <p className="text-xs text-slate-600">No cases yet. Create a draft above.</p>
            ) : (
              <ul className="space-y-2">
                {cases.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedCaseId(c.id)}
                      className={`w-full text-left rounded border p-2 text-xs ${
                        selectedCase?.id === c.id
                          ? "border-slate-900 bg-slate-100"
                          : "border-slate-200 hover:bg-slate-50"
                      }`}
                    >
                      <div className="font-semibold flex justify-between">
                        <span>{c.case_code}</span>
                        <span className="uppercase">{c.status}</span>
                      </div>
                      <div className="text-slate-700 capitalize">{c.platform} &middot; {c.summary_title ?? "Untitled"}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section className="lg:col-span-2 space-y-4">
          <VoiceRecorderPanel
            capabilities={capabilities}
            selectedCase={selectedCase}
            onEnsureDraftCase={handleEnsureVoiceDraftCase}
            onCaseUpdated={replaceCaseInList}
            onClearGlobalMessages={() => {
              setError(null);
              setStatusMessage(null);
            }}
          />

          {!selectedCase ? (
            <div className="rounded border border-slate-300 bg-white p-6 text-sm text-slate-600">
              Select or create a Payment / Incentive Discrepancy case to review details, upload evidence,
              check verified routing, and track its timeline.
            </div>
          ) : (
            <>
              <div className="rounded border border-slate-300 bg-white p-4 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                  <div>
                    <h2 className="text-lg font-semibold">
                      {selectedCase.case_code} &mdash;{" "}
                      <span className="uppercase text-sm">{selectedCase.status}</span>
                    </h2>
                    <p className="text-xs text-slate-600">
                      {selectedCase.confirmed_at
                        ? `Worker confirmed on ${new Date(selectedCase.confirmed_at).toLocaleString()}`
                        : "Draft state: requires explicit worker confirmation."}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDeleteCase(selectedCase.id)}
                    className="rounded border border-red-300 px-2.5 py-1 text-xs text-red-700"
                  >
                    Delete Case
                  </button>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                  <label className="block">
                    Platform
                    <select
                      value={editPlatform}
                      onChange={(e) => setEditPlatform(e.target.value as PlatformName)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    >
                      <option value="swiggy">Swiggy</option>
                      <option value="zomato">Zomato</option>
                      <option value="rapido">Rapido</option>
                      <option value="uber">Uber</option>
                      <option value="other">Other</option>
                    </select>
                  </label>
                  <label className="block">
                    Jurisdiction (blank = national)
                    <input
                      type="text"
                      value={editJurisdiction}
                      onChange={(e) => setEditJurisdiction(e.target.value)}
                      placeholder="national (or state name)"
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    Summary Title
                    <input
                      type="text"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    City
                    <input
                      type="text"
                      value={editCity}
                      onChange={(e) => setEditCity(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    Payout Cycle
                    <input
                      type="text"
                      value={editCycleRaw}
                      onChange={(e) => setEditCycleRaw(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    Incentive Scheme
                    <input
                      type="text"
                      value={editScheme}
                      onChange={(e) => setEditScheme(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    Expected INR
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={editExpected}
                      onChange={(e) => setEditExpected(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    Received INR
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={editReceived}
                      onChange={(e) => setEditReceived(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    Discrepancy INR (optional)
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={editDiscrepancy}
                      onChange={(e) => setEditDiscrepancy(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    Prior Ticket ID (optional)
                    <input
                      type="text"
                      value={editTicketId}
                      onChange={(e) => setEditTicketId(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                  </label>
                </div>
                <label className="block text-xs">
                  Issue Description
                  <textarea
                    rows={2}
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    className="mt-1 w-full rounded border border-slate-300 p-1.5 text-sm"
                  />
                </label>

                {selectedCase.missing_fields.length > 0 && (
                  <div className="rounded bg-amber-50 border border-amber-200 p-2 text-xs text-amber-900">
                    <div className="font-semibold">
                      Unfilled / Null Fields ({selectedCase.missing_fields.length}):
                    </div>
                    <ul className="list-disc pl-4 mt-1">
                      {selectedCase.missing_fields.map((m) => (
                        <li key={m.field}>
                          <code>{m.field}</code> ({m.importance}): {m.prompt}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {selectedCase.status === "draft" ? (
                  <div className="space-y-2 border-t pt-2">
                    <input
                      type="text"
                      placeholder="Optional worker confirmation note"
                      value={confirmNote}
                      onChange={(e) => setConfirmNote(e.target.value)}
                      className="w-full rounded border border-slate-300 p-1.5 text-sm"
                    />
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={handleSaveDraft}
                        className="rounded border border-slate-400 px-3 py-1.5 text-sm"
                      >
                        Save Draft
                      </button>
                      <button
                        type="button"
                        onClick={handleConfirmCase}
                        className="rounded bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white"
                      >
                        Confirm Case Details
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2 border-t pt-2">
                    <div className="flex flex-wrap gap-2">
                      <input
                        type="text"
                        placeholder="Amendment reason (required to edit confirmed facts)"
                        value={amendReason}
                        onChange={(e) => setAmendReason(e.target.value)}
                        className="flex-1 min-w-[200px] rounded border border-slate-300 p-1.5 text-sm"
                      />
                      <button
                        type="button"
                        onClick={handleAmendConfirmed}
                        className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white"
                      >
                        Amend Confirmed Case
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <div className="rounded border border-slate-300 bg-white p-4 space-y-3">
                <div className="flex justify-between items-center">
                  <h3 className="font-semibold">Payment Discrepancy Evidence Checklist</h3>
                  <span className="text-xs text-slate-600">
                    Essential: {selectedCase.evidence_checklist.essential_uploaded}/
                    {selectedCase.evidence_checklist.essential_total} &middot; Helpful:{" "}
                    {selectedCase.evidence_checklist.helpful_uploaded}/
                    {selectedCase.evidence_checklist.helpful_total}
                  </span>
                </div>
                <ul className="space-y-1.5 text-xs">
                  {selectedCase.evidence_checklist.items.map((item) => (
                    <li
                      key={item.key}
                      className="rounded border border-slate-200 p-2 flex justify-between items-start gap-2"
                    >
                      <div>
                        <span className="font-semibold">{item.label}</span>{" "}
                        <span className="uppercase text-[11px] text-slate-600">[{item.priority}]</span>
                        <div className="text-slate-600">{item.guidance}</div>
                      </div>
                      <span
                        className={`px-2 py-0.5 rounded font-medium ${
                          item.status === "uploaded"
                            ? "bg-emerald-100 text-emerald-900"
                            : "bg-amber-100 text-amber-900"
                        }`}
                      >
                        {item.status} ({item.uploaded_count})
                      </span>
                    </li>
                  ))}
                </ul>

                <form onSubmit={handleUploadEvidence} className="border-t pt-3 flex flex-wrap gap-2 items-center text-xs">
                  <select
                    value={evidenceCategory}
                    onChange={(e) => setEvidenceCategory(e.target.value as EvidenceCategory)}
                    className="rounded border border-slate-300 p-1.5"
                  >
                    <option value="payout_summary">Payout summary</option>
                    <option value="incentive_offer">Incentive scheme / offer details</option>
                    <option value="order_or_trip_records">Relevant order or trip records</option>
                    <option value="support_communication">Previous support communication</option>
                    <option value="other">Other</option>
                  </select>
                  <input
                    type="file"
                    accept=".png,.jpg,.jpeg,.webp,.pdf"
                    onChange={(e) => setEvidenceFile(e.target.files?.[0] ?? null)}
                    className="text-xs"
                  />
                  <input
                    type="text"
                    placeholder="Note (optional)"
                    value={evidenceNote}
                    onChange={(e) => setEvidenceNote(e.target.value)}
                    className="rounded border border-slate-300 p-1.5 flex-1 min-w-[140px]"
                  />
                  <button
                    type="submit"
                    disabled={!evidenceFile}
                    className="rounded bg-slate-900 px-3 py-1.5 text-white disabled:bg-slate-300"
                  >
                    Upload Evidence
                  </button>
                </form>

                {selectedCase.evidence_items.length > 0 && (
                  <ul className="space-y-1 text-xs border-t pt-2">
                    {selectedCase.evidence_items.map((ev) => (
                      <li key={ev.id} className="flex justify-between items-center gap-2">
                        <span>
                          [{ev.evidence_category}] {ev.original_filename} ({Math.round(ev.size_bytes / 1024) || 1} KB)
                        </span>
                        <span className="flex gap-2">
                          <a
                            href={`/api/cases/${selectedCase.id}/evidence/${ev.id}/file`}
                            className="underline text-slate-700"
                          >
                            Download
                          </a>
                          <button
                            type="button"
                            onClick={() => handleDeleteEvidence(ev.id)}
                            className="text-red-700 underline"
                          >
                            Delete
                          </button>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded border border-slate-300 bg-white p-4 space-y-3">
                <div className="flex justify-between items-center">
                  <h3 className="font-semibold">Verified Grievance Routing</h3>
                  <span className="text-xs font-medium">
                    {selectedCase.routing.status_message}
                  </span>
                </div>

                {selectedCase.routing.route_status === "requires_verification" && (
                  <div className="rounded border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900">
                    <div className="font-semibold">Route requires verification</div>
                    <div>{selectedCase.routing.reason}</div>
                  </div>
                )}

                {[...selectedCase.routing.platform_channels, ...selectedCase.routing.statutory_channels].map(
                  (ch) => (
                    <div key={ch.id} className="rounded border border-slate-200 p-2.5 text-xs space-y-1">
                      <div className="font-semibold">
                        [{ch.tier}] {ch.channel_name}
                      </div>
                      <div>
                        Contact: <code>{ch.contact_value}</code>
                      </div>
                      <div className="text-slate-700">{ch.instructions}</div>
                      <div className="text-slate-600">
                        Source scope: {ch.what_source_confirms}
                      </div>
                      <div className="text-slate-500">
                        Verified: {ch.last_verified_at} &middot; Source:{" "}
                        <a href={ch.source_url} target="_blank" rel="noreferrer" className="underline">
                          {ch.source_url}
                        </a>
                      </div>
                      <div className="text-slate-500 italic">{ch.disclaimer_text}</div>
                    </div>
                  )
                )}
              </div>

              <div className="rounded border border-slate-300 bg-white p-4 space-y-3">
                <h3 className="font-semibold">Append-Only Case Timeline ({selectedCase.timeline.length})</h3>
                <ul className="space-y-1.5 text-xs">
                  {selectedCase.timeline.map((ev) => {
                    const linkedNames = (ev.linked_evidence_ids ?? [])
                      .map((eid) => selectedCase.evidence_items.find((item) => item.id === eid)?.original_filename ?? eid);
                    return (
                      <li key={ev.id} className="border-l-2 border-slate-400 pl-2.5">
                        <div className="font-medium flex flex-wrap items-center gap-1.5">
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                              ev.event_source === "system"
                                ? "bg-slate-200 text-slate-800"
                                : "bg-blue-100 text-blue-900"
                            }`}
                          >
                            {ev.event_source === "system" ? "System-recorded" : "Worker-reported"}
                          </span>
                          <span>{ev.title}</span>
                          <span className="text-slate-500">({ev.event_type})</span>
                        </div>
                        {ev.event_type === "worker_reported_submission" && (
                          <div className="text-amber-800">
                            Self-reported by worker without attached proof; does not confirm platform receipt or acknowledgement.
                          </div>
                        )}
                        {ev.event_type === "evidence_backed_submission" && (
                          <div className="text-slate-700">
                            Worker submission backed by uploaded evidence; does not confirm platform acknowledgement.
                          </div>
                        )}
                        {ev.notes && <div className="text-slate-700">{ev.notes}</div>}
                        {ev.external_reference_id && (
                          <div className="text-slate-700">Ref ID: {ev.external_reference_id}</div>
                        )}
                        {linkedNames.length > 0 && (
                          <div className="text-slate-700">Linked evidence: {linkedNames.join(", ")}</div>
                        )}
                        <div className="text-slate-500">{new Date(ev.created_at).toLocaleString()}</div>
                      </li>
                    );
                  })}
                </ul>

                <form onSubmit={handleAppendMilestone} className="border-t pt-3 space-y-2 text-xs">
                  <div className="flex flex-wrap gap-2">
                    <select
                      value={milestoneType}
                      onChange={(e) => setMilestoneType(e.target.value as typeof milestoneType)}
                      className="rounded border border-slate-300 p-1.5"
                    >
                      <option value="worker_reported_submission">
                        Worker-reported submission (unverified)
                      </option>
                      <option value="evidence_backed_submission">
                        Submission supported by recorded evidence
                      </option>
                      <option value="platform_acknowledgement_reported">
                        Worker-reported platform acknowledgement
                      </option>
                      <option value="platform_response_reported">Worker-reported platform response</option>
                      <option value="escalation_reported">Worker-reported escalation</option>
                      <option value="closed">Case closed</option>
                      <option value="note_added">Worker note</option>
                    </select>
                    <input
                      type="text"
                      required
                      placeholder="Milestone title"
                      value={milestoneTitle}
                      onChange={(e) => setMilestoneTitle(e.target.value)}
                      className="rounded border border-slate-300 p-1.5 flex-1 min-w-[160px]"
                    />
                    <input
                      type="text"
                      placeholder="Ticket/Ref ID (optional)"
                      value={milestoneRef}
                      onChange={(e) => setMilestoneRef(e.target.value)}
                      className="rounded border border-slate-300 p-1.5"
                    />
                    <input
                      type="text"
                      placeholder="Notes (optional)"
                      value={milestoneNotes}
                      onChange={(e) => setMilestoneNotes(e.target.value)}
                      className="rounded border border-slate-300 p-1.5 flex-1 min-w-[140px]"
                    />
                    <button type="submit" className="rounded bg-slate-900 px-3 py-1.5 text-white">
                      Log Event
                    </button>
                  </div>

                  {selectedCase.evidence_items.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2 text-slate-700">
                      <span>Link uploaded evidence:</span>
                      {selectedCase.evidence_items.map((ev) => (
                        <label key={ev.id} className="inline-flex items-center gap-1">
                          <input
                            type="checkbox"
                            checked={milestoneEvidenceIds.includes(ev.id)}
                            onChange={() => toggleMilestoneEvidence(ev.id)}
                          />
                          <span>{ev.original_filename}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </form>
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  );
}

export default App;
