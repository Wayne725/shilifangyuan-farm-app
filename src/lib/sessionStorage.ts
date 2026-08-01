import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

import type { AuthSession } from "../types";

const SESSION_KEY = "shilifangyuan.session";

/**
 * Keeps the signed-in session across reloads.
 *
 * This matters most on Web: paying goes through a top-level navigation to
 * ECPay, so without persistence the buyer comes back signed out and cannot see
 * the order they just paid for.
 */
async function readRaw(): Promise<string | null> {
  if (Platform.OS === "web") {
    try {
      return globalThis.localStorage?.getItem(SESSION_KEY) ?? null;
    } catch {
      return null;
    }
  }
  return SecureStore.getItemAsync(SESSION_KEY);
}

async function writeRaw(value: string | null) {
  if (Platform.OS === "web") {
    try {
      if (value === null) globalThis.localStorage?.removeItem(SESSION_KEY);
      else globalThis.localStorage?.setItem(SESSION_KEY, value);
    } catch {
      // Private browsing can reject writes; the session simply stays in memory.
    }
    return;
  }
  if (value === null) await SecureStore.deleteItemAsync(SESSION_KEY);
  else await SecureStore.setItemAsync(SESSION_KEY, value);
}

export async function loadStoredSession(): Promise<AuthSession | null> {
  const raw = await readRaw();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as AuthSession;
    return parsed?.access_token ? parsed : null;
  } catch {
    await writeRaw(null);
    return null;
  }
}

export async function storeSession(session: AuthSession | null) {
  await writeRaw(session ? JSON.stringify(session) : null);
}
