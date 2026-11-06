import { canonicalEntryUrl } from "./lib/canonical-entry";

let hasExistingDraft = false;
try {
  const cart: unknown = JSON.parse(window.localStorage.getItem("slf_cart") || "[]");
  hasExistingDraft = (Array.isArray(cart) && cart.length > 0)
    || Object.keys(window.sessionStorage).some(key => key.startsWith("slf_meal_draft_v1:"));
} catch {
  // Preserve an existing session when its local checkout state cannot be inspected.
  hasExistingDraft = true;
}
const destination = canonicalEntryUrl(window.location.href, hasExistingDraft);
if (destination) {
  window.location.replace(destination);
} else {
  void import("./main");
}
