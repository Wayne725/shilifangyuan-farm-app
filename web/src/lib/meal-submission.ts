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
