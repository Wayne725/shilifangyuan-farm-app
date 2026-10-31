import { readStoredJson, sessionStore, writeStoredJson } from "./browser-storage";

export interface ProposalInput {
  title: string;
  body: string;
  proposal_type: "resolution" | "multiple_choice";
  options: { label: string }[];
}
export interface ProposalSubmission {
  requestId: string;
  input: ProposalInput;
  createdId?: string;
  createUncertain?: boolean;
}
const storageKey = (userId: string) => `slf_proposal_submission:${userId}`;

export function readProposalSubmission(userId: string): ProposalSubmission | null {
  const saved = readStoredJson<ProposalSubmission | null>(sessionStore, storageKey(userId), null);
  return saved && typeof saved.requestId === "string" && typeof saved.input?.title === "string"
    && typeof saved.input.body === "string" && ["resolution", "multiple_choice"].includes(saved.input.proposal_type)
    && Array.isArray(saved.input.options) ? saved : null;
}

export function prepareProposalSubmission(userId: string, input: ProposalInput): ProposalSubmission {
  const submission = readProposalSubmission(userId) ?? { requestId: crypto.randomUUID(), input };
  if (!writeStoredJson(sessionStore, storageKey(userId), submission)) {
    throw new Error("瀏覽器無法儲存提案識別，請允許網站儲存資料後重試");
  }
  return submission;
}

export function recordCreatedProposal(userId: string, submission: ProposalSubmission, createdId: string): ProposalSubmission {
  const next = { ...submission, createdId };
  writeStoredJson(sessionStore, storageKey(userId), next);
  return next;
}

export function markProposalCreateUncertain(userId: string, submission: ProposalSubmission): void {
  writeStoredJson(sessionStore, storageKey(userId), { ...submission, createUncertain: true });
}

export function clearProposalSubmission(userId: string, requestId: string): void {
  if (readProposalSubmission(userId)?.requestId === requestId) sessionStore.removeItem(storageKey(userId));
}
