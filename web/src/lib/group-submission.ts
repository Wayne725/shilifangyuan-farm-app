import { readStoredJson, sessionStore, writeStoredJson, type SafeBrowserStorage } from "./browser-storage";
import type { GroupCheckoutInput } from "./commerce";

export interface GroupQuote {
  confirmation_fingerprint: string;
  membership_type: string;
  member_tax_exempt?: boolean;
  tax_amount?: number;
  tax_type?: "taxable" | "tax_exempt";
  quantity: number;
  unit_price: number;
  product_subtotal: number;
  shipping_fee: number;
  amount_total: number;
}

export interface GroupSubmission {
  requestId: string;
  input: GroupCheckoutInput & { expectedTotal: number; expectedQuoteFingerprint: string };
  quote: GroupQuote;
  title: string;
  pickupLocationName?: string;
}

const storageKeyPrefix = "slf_group_submission:";
const storageKey = (userId: string, campaignId: string) => `${storageKeyPrefix}${userId}:${campaignId}`;
export const groupQuoteIsConfirmed = (quote?: GroupQuote): quote is GroupQuote => Boolean(quote
  && /^[0-9a-f]{64}$/.test(quote.confirmation_fingerprint) && Number.isInteger(quote.amount_total) && quote.amount_total >= 0);

export function readGroupSubmission(userId: string, campaignId: string, storage: Pick<SafeBrowserStorage, "getItem"> = sessionStore): GroupSubmission | null {
  const saved = readStoredJson<GroupSubmission | null>(storage, storageKey(userId, campaignId), null);
  const input = saved?.input;
  return saved && typeof saved.requestId === "string" && typeof saved.title === "string"
    && input?.campaignId === campaignId && Number.isInteger(input.quantity) && input.quantity > 0
    && typeof input.contactEmail === "string" && typeof input.invoicePreference?.buyerEmail === "string"
    && ["personal", "company"].includes(input.invoicePreference.buyerType)
    && ["pickup", "shipping"].includes(input.fulfillment?.kind)
    && groupQuoteIsConfirmed(saved.quote) && input.expectedTotal === saved.quote.amount_total
    && input.expectedQuoteFingerprint === saved.quote.confirmation_fingerprint ? saved : null;
}

export function hasGroupSubmissionForPath(pathname: string, storage: Storage): boolean {
  const match = /^\/groups\/([^/]+)\/?$/.exec(pathname);
  if (!match) return false;

  let campaignId: string;
  try {
    campaignId = decodeURIComponent(match[1]);
  } catch {
    return false;
  }
  if (/[:/]/.test(campaignId)) return false;

  return Object.keys(storage).some(key => {
    if (!key.startsWith(storageKeyPrefix)) return false;
    const [userId, savedCampaignId, extra] = key.slice(storageKeyPrefix.length).split(":");
    return Boolean(userId && savedCampaignId === campaignId && extra === undefined
      && readGroupSubmission(userId, campaignId, storage));
  });
}

export function prepareGroupSubmission(userId: string, input: GroupCheckoutInput, quote: GroupQuote,
  display: { title: string; pickupLocationName?: string }, storage: SafeBrowserStorage = sessionStore,
  newId = () => crypto.randomUUID()): GroupSubmission {
  const key = storageKey(userId, input.campaignId);
  const existing = readGroupSubmission(userId, input.campaignId, storage);
  if (!existing && storage.getItem(key) !== null) throw new Error("共同購買送單資料無法讀取，請先到訂單中心核對");
  if (!existing && !groupQuoteIsConfirmed(quote)) throw new Error("報價識別尚未確認，請重新取得報價");
  const { saveCarrier: _saveCarrier, ...invoicePreference } = input.invoicePreference;
  const { clientRequestId: _requestId, ...frozenInput } = input;
  const submission = existing ?? { requestId: newId(), input: { ...frozenInput,
    invoicePreference, fulfillment: { ...input.fulfillment }, expectedTotal: quote.amount_total,
    expectedQuoteFingerprint: quote.confirmation_fingerprint }, quote: { ...quote }, ...display };
  if (!writeStoredJson(storage, key, submission)) throw new Error("瀏覽器無法儲存共同購買送單識別，請允許網站儲存資料後重試");
  return submission;
}

export function clearGroupSubmission(userId: string, campaignId: string, requestId: string, storage: SafeBrowserStorage = sessionStore): void {
  if (readGroupSubmission(userId, campaignId, storage)?.requestId === requestId) storage.removeItem(storageKey(userId, campaignId));
}

export function groupSubmissionWasRejected(reason: unknown, firstAttempt = false): boolean {
  if (!(reason instanceof Error) || !("status" in reason)) return false;
  return (reason.status === 409 && "code" in reason && reason.code === "quote_changed") || (reason.status === 422 && firstAttempt);
}
