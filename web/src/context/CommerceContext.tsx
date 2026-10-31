import {
  createContext,
  type ReactNode,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

import type { CartItem, Product } from "../lib/types";
import { maxOrderItemQuantity, productQuantityLimit } from "../lib/cart-quantity";
import { localStore, readStoredJson } from "../lib/browser-storage";
import { cartMatchesSubmission } from "../lib/cart-snapshot";

interface CommerceContextValue {
  cart: CartItem[];
  itemCount: number;
  addToCart: (product: Product, quantity?: number) => void;
  adjustQuantity: (productId: string, delta: number) => void;
  clearCart: (expectedItems?: { product_id: string; quantity: number }[]) => void;
}

const storageKey = "slf_cart";
const CommerceContext = createContext<CommerceContextValue | null>(null);

function readCart(): CartItem[] {
  const value = readStoredJson<unknown>(localStore, storageKey, []);
  return Array.isArray(value) ? value.filter((item): item is CartItem => Boolean(item?.product && typeof item.product.id === "string" && Number.isInteger(item.quantity) && item.quantity > 0)) : [];
}

export function CommerceProvider({ children }: { children: ReactNode }) {
  const [cart, setCart] = useState<CartItem[]>(readCart);
  const currentCart = useRef(cart);

  const persist = (items: CartItem[]) => {
    localStore.setItem(storageKey, JSON.stringify(items));
    currentCart.current = items;
    setCart(items);
  };

  const value = useMemo<CommerceContextValue>(
    () => ({
      cart,
      itemCount: cart.reduce((sum, item) => sum + item.quantity, 0),
      addToCart(product, quantity = 1) {
        const current = currentCart.current;
        const existing = current.find((item) => item.product.id === product.id);
        const remaining = Math.max(0, productQuantityLimit(product) - (existing?.quantity || 0));
        if (!Number.isInteger(quantity) || quantity < 1 || quantity > maxOrderItemQuantity) {
          throw new RangeError(`請輸入 1 至 ${maxOrderItemQuantity} 的整數數量`);
        }
        if (quantity > remaining) throw new RangeError(`目前最多還可加入 ${remaining} ${product.unit}，請重新選擇數量`);
        persist(existing
          ? current.map((item) =>
              item.product.id === product.id
                ? { product, quantity: item.quantity + quantity }
                : item,
            )
          : [...current, { product, quantity }]);
      },
      adjustQuantity(productId, delta) {
        const current = currentCart.current;
        const existing = current.find((item) => item.product.id === productId);
        if (!existing) return;
        const quantity = existing.quantity + delta;
        if (!Number.isInteger(delta) || !Number.isInteger(quantity) || quantity < 0) {
          throw new RangeError("請輸入有效的整數數量");
        }
        const limit = maxOrderItemQuantity;
        if (quantity > existing.quantity && quantity > limit) {
          throw new RangeError(`此商品目前可購上限為 ${limit} ${existing.product.unit}`);
        }
        persist(current
            .map((item) =>
              item.product.id === productId ? { ...item, quantity } : item,
            )
            .filter((item) => item.quantity > 0),
        );
      },
      clearCart(expectedItems) {
        if (expectedItems && !cartMatchesSubmission(currentCart.current, expectedItems)) return;
        persist([]);
      },
    }),
    [cart],
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
