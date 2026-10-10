const accountKey = "shilifangyuan.remembered-login-email";

export function rememberedLoginEmail(): string {
  try { return localStorage.getItem(accountKey) || ""; } catch { return ""; }
}

export function rememberLoginEmail(email: string, enabled: boolean): void {
  try {
    if (enabled) localStorage.setItem(accountKey, email.trim());
    else localStorage.removeItem(accountKey);
  } catch { /* Browser storage may be disabled. Login can still continue. */ }
}
