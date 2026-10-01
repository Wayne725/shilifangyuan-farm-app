import {
  createContext,
  type ReactNode,
  useContext,
  useMemo,
  useState,
} from "react";

import type { CartItem, Product } from "../lib/types";

interface CommerceContextValue {
  cart: CartItem[];
  itemCount: number;
  addToCart: (product: Product) => void;
  changeQuantity: (productId: string, quantity: number) => void;
  clearCart: () => void;
}

const storageKey = "slf_cart";
const CommerceContext = createContext<CommerceContextValue | null>(null);

function readCart(): CartItem[] {
  const value = localStorage.getItem(storageKey);
  if (!value) return [];
  try {
    return JSON.parse(value) as CartItem[];
  } catch {
    return [];
  }
}

export function CommerceProvider({ children }: { children: ReactNode }) {
  const [cart, setCart] = useState<CartItem[]>(readCart);

  const persist = (items: CartItem[]) => {
    setCart(items);
    localStorage.setItem(storageKey, JSON.stringify(items));
  };

  const value = useMemo<CommerceContextValue>(
    () => ({
      cart,
      itemCount: cart.reduce((sum, item) => sum + item.quantity, 0),
      addToCart(product) {
        const existing = cart.find((item) => item.product.id === product.id);
        const next = existing
          ? cart.map((item) =>
              item.product.id === product.id
                ? { ...item, quantity: item.quantity + 1 }
                : item,
            )
          : [...cart, { product, quantity: 1 }];
        persist(next);
      },
      changeQuantity(productId, quantity) {
        persist(
          cart
            .map((item) =>
              item.product.id === productId ? { ...item, quantity } : item,
            )
            .filter((item) => item.quantity > 0),
        );
      },
      clearCart() {
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
