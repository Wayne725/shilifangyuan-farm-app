import type { SafeBrowserStorage } from "./browser-storage";
import type { beginMealCheckout } from "./commerce";

type MealSubmission = Omit<Parameters<typeof beginMealCheckout>[0], "clientRequestId">;
const submissionPrefix = "meal-submission:";
const submissionKeyPattern = /^meal-submission:[0-9a-f]{64}$/;
const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const markerKey = (key: string) => `meal-submission-current:${key.slice(submissionPrefix.length)}`;

export function mealSubmissionFingerprint(userId: string, input: MealSubmission): string {
  const { saveCarrier: _saveCarrier, ...invoicePreference } = input.invoicePreference;
  const { invoiceHandling, mealBookingType, contactPhone: _contactPhone, ...orderInput } = input;
  const legacyInvoiceHandling = input.paymentMethod === "cash_on_pickup" ? "external" : "on_payment";
  // Preserve retry keys across checkout preference and contact form changes.
  return JSON.stringify({ userId, ...orderInput,
    ...(mealBookingType === "business" ? { mealBookingType } : {}),
    ...(invoiceHandling && invoiceHandling !== legacyInvoiceHandling ? { invoiceHandling } : {}), invoicePreference });
}

export async function mealSubmissionStorageKey(userId: string, input: MealSubmission): Promise<string> {
  const fingerprint = mealSubmissionFingerprint(userId, input);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(fingerprint));
  return `${submissionPrefix}${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

export function unmatchedMealSubmissions(excludedKeys: readonly string[]): string {
  const unmatched: string[] = [];
  try {
    const storage = window.sessionStorage;
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key || !submissionKeyPattern.test(key) || excludedKeys.includes(key)) continue;
      const id = storage.getItem(key);
      if (id && requestIdPattern.test(id) && storage.getItem(markerKey(key)) !== id) {
        unmatched.push(`${key}:${id}`);
      }
    }
  } catch {
    throw new Error("無法讀取便當送單紀錄，請允許瀏覽器儲存資料後重試");
  }
  return unmatched.sort().join("|");
}

export function rememberMealSubmission(storage: SafeBrowserStorage, key: string, id: string, fresh: boolean): void {
  if (!storage.setItem(key, id)) throw new Error("瀏覽器無法儲存送單識別，請允許網站儲存資料後重試");
  // Keep the original key for an older page. The marker identifies only IDs this version created.
  if (fresh) storage.setItem(markerKey(key), id);
}

export function forgetMealSubmission(storage: SafeBrowserStorage, key: string, id: string): void {
  if (storage.getItem(key) === id) storage.removeItem(key);
  if (storage.getItem(markerKey(key)) === id) storage.removeItem(markerKey(key));
}
