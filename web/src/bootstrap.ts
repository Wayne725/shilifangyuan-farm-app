import { canonicalEntryUrl } from "./lib/canonical-entry";
import { hasGroupSubmissionForPath } from "./lib/group-submission";

let hasExistingDraft = false;
try {
  hasExistingDraft = Object.keys(window.localStorage).some(key => {
    if (key !== "slf_cart" && !key.startsWith("slf_cart_v2:")) return false;
    const value = JSON.parse(window.localStorage.getItem(key) || "[]");
    const items = Array.isArray(value) ? value : value?.items;
    return Array.isArray(items) && items.length > 0;
  })
    || Object.keys(window.sessionStorage).some(key => key.startsWith("slf_meal_draft_v1:"))
    || hasGroupSubmissionForPath(window.location.pathname, window.sessionStorage);
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
