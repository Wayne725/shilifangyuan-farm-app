import type { beginMealCheckout } from "./commerce";

type MealSubmission = Omit<Parameters<typeof beginMealCheckout>[0], "clientRequestId">;

export function mealSubmissionFingerprint(userId: string, input: MealSubmission): string {
  const { saveCarrier: _saveCarrier, ...invoicePreference } = input.invoicePreference;
  // Preserve existing retry keys; saving an account preference is not a new order.
  return JSON.stringify({ userId, ...input, invoicePreference });
}
