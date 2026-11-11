import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

import type { CartItem, Product } from "../lib/types";
import { maxOrderItemQuantity, productQuantityLimit } from "../lib/cart-quantity";
import { localStore } from "../lib/browser-storage";
import { cartMatchesSubmission } from "../lib/cart-snapshot";
import { createAccountCartStore } from "../lib/cart-account";
import { subscribeSession } from "../lib/api";
import { useAuth } from "./AuthContext";

interface CommerceContextValue {
  cart: CartItem[];
  itemCount: number;
  addToCart: (product: Product, quantity?: number) => void;
  adjustQuantity: (productId: string, delta: number, purchaseLimit?: number | null) => void;
  removeFromCart: (productId: string) => void;
  clearCart: (expectedItems?: { product_id: string; quantity: number }[]) => void;
}

const CommerceContext = createContext<CommerceContextValue | null>(null);

export function CommerceProvider({ children }: { children: ReactNode }) {
  const { user, isAuthReady } = useAuth();
  const [store] = useState(() => createAccountCartStore(localStore));
  const { scope: storedScope, items } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const scope = isAuthReady && storedScope?.ownerId === (user?.id ?? null) ? storedScope : null;
  const cart = scope ? items : [];
  useEffect(() => subscribeSession(next => store.setOwner(next?.id ?? null)), [store]);
  useEffect(() => { if (isAuthReady && store.getSnapshot().scope === null) store.setOwner(user?.id ?? null); }, [store, user?.id, isAuthReady]);
  useEffect(() => {
    const sync = (event: StorageEvent) => store.syncStorage(event.key);
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [store]);

  const value = useMemo<CommerceContextValue>(
    () => ({
      cart,
      itemCount: cart.reduce((sum, item) => sum + item.quantity, 0),
      addToCart(product, quantity = 1) {
        const changed = store.update(scope, current => {
          const existing = current.find((item) => item.product.id === product.id);
          const remaining = Math.max(0, productQuantityLimit(product) - (existing?.quantity || 0));
          if (!Number.isInteger(quantity) || quantity < 1 || quantity > maxOrderItemQuantity) {
            throw new RangeError(`請輸入 1 至 ${maxOrderItemQuantity} 的整數數量`);
          }
          if (quantity > remaining) throw new RangeError(`目前最多還可加入 ${remaining} ${product.unit}，請重新選擇數量`);
          return existing
            ? current.map((item) =>
                item.product.id === product.id
                  ? { product, quantity: item.quantity + quantity }
                  : item,
              )
            : [...current, { product, quantity }];
        });
        if (!changed) throw new Error("登入狀態已變更，請稍候再加入商品");
      },
      adjustQuantity(productId, delta, purchaseLimit = null) {
        const changed = store.update(scope, current => {
          const existing = current.find((item) => item.product.id === productId);
          if (!existing) return current;
          const quantity = existing.quantity + delta;
          if (!Number.isInteger(delta) || !Number.isInteger(quantity) || quantity < 0) {
            throw new RangeError("請輸入有效的整數數量");
          }
          if (delta > 0 && purchaseLimit === null) throw new RangeError("請先確認商品庫存");
          if (delta > 0 && purchaseLimit !== null && quantity > Math.min(purchaseLimit, maxOrderItemQuantity)) {
            throw new RangeError(`此商品目前可購上限為 ${purchaseLimit} ${existing.product.unit}`);
          }
          return current
              .map((item) =>
                item.product.id === productId ? { ...item, quantity } : item,
              )
              .filter((item) => item.quantity > 0);
        });
        if (!changed) throw new Error("登入狀態已變更，請重新確認結帳清單");
      },
      removeFromCart(productId) {
        const changed = store.update(scope, current => current.filter(item => item.product.id !== productId));
        if (!changed) throw new Error("登入狀態已變更，請重新確認結帳清單");
      },
      clearCart(expectedItems) {
        store.update(scope, current => expectedItems && !cartMatchesSubmission(current, expectedItems) ? current : []);
      },
    }),
    [cart, scope, store],
  );

  return (
    <CommerceContext.Provider value={value}>
      {children}
    </CommerceContext.Provider>
  );
}

export function useCommerce(): CommerceContextValue {
  const context = useContext(CommerceContext);
  if (!context) throw new Error("CommerceProvider is missing");
  return context;
}
