export function loginReturnTo(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")
    || value.includes("\\") || /[\u0000-\u001f]/.test(value)) return "/shop";
  const target = new URL(value, "https://return.invalid");
  if (target.origin !== "https://return.invalid") return "/shop";
  if (["/login", "/line-consent", "/auth/line/callback"].includes(target.pathname)) return "/account";
  if (["/register", "/verify-email", "/forgot-password", "/reset-password"].includes(target.pathname)) return "/";
  return `${target.pathname}${target.search}${target.hash}`;
}
