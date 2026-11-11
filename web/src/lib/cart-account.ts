import type { CartItem } from "./types";
import { maxOrderItemQuantity } from "./cart-quantity";
import type { SafeBrowserStorage } from "./browser-storage";

export const cartStoragePrefix = "slf_cart_v2:";
export const cartStorageKey = (ownerId: string | null) => `${cartStoragePrefix}${ownerId === null ? "guest" : `user:${encodeURIComponent(ownerId)}`}`;

interface CartRecord {
  items: CartItem[];
  transferId?: string;
  consumedGuestId?: string;
}
interface CartScope { ownerId: string | null }
export interface AccountCartSnapshot { scope: CartScope | null; items: CartItem[] }

function readRecord(storage: SafeBrowserStorage, key: string): CartRecord {
  try {
    const value = JSON.parse(storage.getItem(key) || "[]");
    const items = Array.isArray(value) ? value : value?.items;
    return {
      items: Array.isArray(items) ? items.filter((item): item is CartItem => Boolean(item?.product && typeof item.product.id === "string" && Number.isInteger(item.quantity) && item.quantity > 0)) : [],
      transferId: typeof value?.transferId === "string" ? value.transferId : undefined,
      consumedGuestId: typeof value?.consumedGuestId === "string" ? value.consumedGuestId : undefined,
    };
  } catch {
    return { items: [] };
  }
}

function mergeGuest(items: CartItem[], guest: CartItem[]): CartItem[] {
  const merged = new Map(items.map(item => [item.product.id, item]));
  for (const item of guest) {
    const existing = merged.get(item.product.id);
    merged.set(item.product.id, { product: item.product, quantity: Math.min(maxOrderItemQuantity, (existing?.quantity || 0) + item.quantity) });
  }
  return [...merged.values()];
}

export function createAccountCartStore(storage: SafeBrowserStorage) {
  let snapshot: AccountCartSnapshot = { scope: null, items: [] };
  const listeners = new Set<() => void>();
  const publish = (scope: CartScope, items: CartItem[]) => {
    snapshot = { scope, items };
    listeners.forEach(listener => listener());
  };
  const write = (key: string, record: CartRecord) => storage.setItem(key, JSON.stringify(record));

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    setOwner(ownerId: string | null) {
      const previous = snapshot.scope;
      if (previous?.ownerId === ownerId) return;
      const key = cartStorageKey(ownerId);
      let record = readRecord(storage, key);
      if (ownerId === null && previous) {
        record = { items: [] };
      } else if (ownerId !== null && (!previous || previous.ownerId === null)) {
        const guest = readRecord(storage, cartStorageKey(null));
        if (guest.items.length) {
          const transferId = guest.transferId || JSON.stringify(guest.items.map(item => [item.product.id, item.quantity]));
          if (record.consumedGuestId !== transferId) record = { items: mergeGuest(record.items, guest.items), consumedGuestId: transferId };
          // The receipt keeps a failed guest cleanup from merging the same cart twice.
          if (write(key, record)) write(cartStorageKey(null), { items: [] });
        }
      }
      write(key, record);
      publish({ ownerId }, record.items);
    },
    update(scope: CartScope | null, change: (items: CartItem[]) => CartItem[]) {
      if (!scope || scope !== snapshot.scope) return false;
      const key = cartStorageKey(scope.ownerId);
      const record = readRecord(storage, key);
      const items = change(record.items);
      if (items === record.items) { publish(scope, items); return true; }
      write(key, { ...record, items, ...(scope.ownerId === null ? { transferId: crypto.randomUUID() } : {}) });
      publish(scope, items);
      return true;
    },
    syncStorage(key: string | null) {
      const scope = snapshot.scope;
      if (scope && (key === null || key === cartStorageKey(scope.ownerId))) publish(scope, readRecord(storage, cartStorageKey(scope.ownerId)).items);
    },
  };
}
