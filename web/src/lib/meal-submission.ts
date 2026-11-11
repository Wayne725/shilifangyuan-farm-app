import type { beginMealCheckout } from "./commerce";

type MealSubmission = Omit<Parameters<typeof beginMealCheckout>[0], "clientRequestId">;

export function mealSubmissionFingerprint(userId: string, input: MealSubmission): string {
  const { saveCarrier: _saveCarrier, ...invoicePreference } = input.invoicePreference;
  const { invoiceHandling, mealBookingType, ...orderInput } = input;
  const legacyInvoiceHandling = input.paymentMethod === "cash_on_pickup" ? "external" : "on_payment";
  // Preserve existing retry keys; saving an account preference is not a new order.
  return JSON.stringify({ userId, ...orderInput,
    ...(mealBookingType === "business" ? { mealBookingType } : {}),
    ...(invoiceHandling && invoiceHandling !== legacyInvoiceHandling ? { invoiceHandling } : {}), invoicePreference });
}

export async function mealSubmissionStorageKey(userId: string, input: MealSubmission): Promise<string> {
  const fingerprint = mealSubmissionFingerprint(userId, input);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(fingerprint));
  return `meal-submission:${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("")}`;
}
