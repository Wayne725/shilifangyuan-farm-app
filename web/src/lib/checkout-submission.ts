import { readStoredJson, sessionStore, writeStoredJson, type SafeBrowserStorage } from "./browser-storage";
import type { CheckoutInput } from "./commerce";
import type { OrderQuote } from "./types";

type ConfirmedCheckout = CheckoutInput & { expectedTotal: number };
export interface CheckoutSubmission {
  requestId: string;
  input: ConfirmedCheckout;
  quote: OrderQuote;
}

const storageKey = (userId: string) => `slf_checkout_submission:${userId}`;

export function readCheckoutSubmission(userId: string, storage: SafeBrowserStorage = sessionStore): CheckoutSubmission | null {
  const saved = readStoredJson<CheckoutSubmission | null>(storage, storageKey(userId), null);
  return saved && typeof saved.requestId === "string" && Array.isArray(saved.input?.items)
    && Number.isInteger(saved.input.expectedTotal) && saved.quote?.amount_total === saved.input.expectedTotal
    ? saved : null;
}

export function prepareCheckoutSubmission(
  userId: string, input: ConfirmedCheckout, quote: OrderQuote,
  storage: SafeBrowserStorage = sessionStore, newId = () => crypto.randomUUID(),
): CheckoutSubmission {
  const existing = readCheckoutSubmission(userId, storage);
  const { saveCarrier: _saveCarrier, ...invoicePreference } = input.invoicePreference;
  const submission = existing ?? {
    requestId: newId(),
    input: { ...input, items: input.items.map(item => ({ ...item })), invoicePreference },
    quote,
  };
  // Persist the frozen request before sending; an unknown result must survive reload.
  if (!writeStoredJson(storage, storageKey(userId), submission)) {
    throw new Error("瀏覽器無法儲存送單識別，請允許網站儲存資料後重試");
  }
  return submission;
}

export function clearCheckoutSubmission(userId: string, requestId: string, storage: SafeBrowserStorage = sessionStore): void {
  if (readCheckoutSubmission(userId, storage)?.requestId === requestId) storage.removeItem(storageKey(userId));
}

export function checkoutWasRejected(reason: unknown): boolean {
  if (!(reason instanceof Error) || !("status" in reason) || typeof reason.status !== "number") return false;
  if (reason.status < 400 || reason.status >= 500 || reason.status === 401 || reason.status === 429) return false;
  return !["此送單識別碼已使用，請重新確認訂單", "登入身分已變更，請確認目前帳號後重新操作。"].includes(reason.message);
}
