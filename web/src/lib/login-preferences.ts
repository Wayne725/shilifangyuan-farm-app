import { localStore } from "./browser-storage";

const accountKey = "shilifangyuan.remembered-login-email";

export function rememberedLoginEmail(): string {
  return localStore.getItem(accountKey) || "";
}

export function rememberLoginEmail(email: string, enabled: boolean): void {
  if (enabled) localStore.setItem(accountKey, email.trim());
  else localStore.removeItem(accountKey);
}
