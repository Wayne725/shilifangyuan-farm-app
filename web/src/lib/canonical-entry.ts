const legacyOrigin = "https://shilifangyuan-web.onrender.com";
const canonicalOrigin = "https://shilifangyuan.com";

export function canonicalEntryUrl(href: string, hasExistingDraft = false): string | null {
  const current = new URL(href);
  if (current.origin !== legacyOrigin || hasExistingDraft) return null;
  // Existing provider returns must finish in the origin that started the flow.
  if (/^\/(?:auth|payments?)(?:\/|$)/.test(current.pathname)
    || ["/verify-email", "/reset-password", "/checkout", "/cart"].includes(current.pathname)) return null;
  if (["/meal-orders", "/orders"].includes(current.pathname)
    && current.searchParams.get("order_id")?.trim()) return null;
  const sensitive = /^(?:code|state|token|access_token|refresh_token|id_token|signature|attempt_id|payment|result|logistics)$/i;
  const fragment = new URLSearchParams(current.hash.slice(1));
  if ([...current.searchParams.keys(), ...fragment.keys()].some(key => sensitive.test(key))) return null;
  const target = new URL(canonicalOrigin);
  target.pathname = current.pathname;
  target.search = current.search;
  target.hash = current.hash;
  return target.href;
}
