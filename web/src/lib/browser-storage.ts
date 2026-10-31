type BrowserStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export interface SafeBrowserStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): boolean;
  removeItem(key: string): boolean;
}

let unavailable = false;
const listeners = new Set<() => void>();
function reportUnavailable() {
  if (unavailable) return;
  unavailable = true;
  listeners.forEach(listener => listener());
}
export const browserStorageStatus = {
  getSnapshot: () => unavailable,
  subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
};

export function createBrowserStorage(resolve: () => BrowserStorage): SafeBrowserStorage {
  const cache = new Map<string, string | null>();
  const baselines = new Map<string, string | null>();
  const overrides = new Map<string, string | null>();
  const unreadable = new Set<string>();
  return {
    getItem(key) {
      if (overrides.has(key)) return overrides.get(key) ?? null;
      try {
        const value = resolve().getItem(key);
        cache.set(key, value);
        baselines.set(key, value);
        return value;
      } catch {
        unreadable.add(key);
        reportUnavailable();
        return cache.get(key) ?? null;
      }
    },
    setItem(key, value) {
      cache.set(key, value);
      try {
        const target = resolve();
        if (unreadable.has(key)) {
          const actual = target.getItem(key);
          if (actual !== (baselines.get(key) ?? null)) {
            overrides.set(key, value);
            reportUnavailable();
            return false;
          }
        }
        target.setItem(key, value);
        baselines.set(key, value);
        unreadable.delete(key);
        overrides.delete(key);
        return true;
      } catch {
        overrides.set(key, value);
        reportUnavailable();
        return false;
      }
    },
    removeItem(key) {
      cache.set(key, null);
      try {
        resolve().removeItem(key);
        baselines.set(key, null);
        unreadable.delete(key);
        overrides.delete(key);
        return true;
      } catch {
        overrides.set(key, null);
        reportUnavailable();
        return false;
      }
    },
  };
}

export const localStore = createBrowserStorage(() => window.localStorage);
export const sessionStore = createBrowserStorage(() => window.sessionStorage);

export function readStoredJson<T>(storage: SafeBrowserStorage, key: string, fallback: T): T {
  try {
    const value = storage.getItem(key);
    return value === null ? fallback : JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

export function writeStoredJson(storage: SafeBrowserStorage, key: string, value: unknown): boolean {
  return storage.setItem(key, JSON.stringify(value));
}
