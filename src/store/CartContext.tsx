import {
  createContext,
  type PropsWithChildren,
  useContext,
  useMemo,
  useState,
} from "react";

import type { CartItem, Product } from "../types";
import { useAuth } from "./AuthContext";

type CartContextValue = {
  items: CartItem[];
  itemCount: number;
  addItem: (productId: string, quantity?: number) => void;
  setQuantity: (productId: string, quantity: number) => void;
  removeItem: (productId: string) => void;
  clear: () => void;
  totalFor: (products: Product[]) => number;
};

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: PropsWithChildren) {
  const [items, setItems] = useState<CartItem[]>([]);
  const { user } = useAuth();

  const addItem = (productId: string, quantity = 1) => {
    setItems((current) => {
      const existing = current.find((item) => item.product_id === productId);
      return existing
        ? current.map((item) =>
            item.product_id === productId
              ? { ...item, quantity: item.quantity + quantity }
              : item,
          )
        : [...current, { product_id: productId, quantity }];
    });
  };

  const setQuantity = (productId: string, quantity: number) => {
    if (quantity <= 0) {
      setItems((current) =>
        current.filter((item) => item.product_id !== productId),
      );
      return;
    }
    setItems((current) =>
      current.map((item) =>
        item.product_id === productId ? { ...item, quantity } : item,
      ),
    );
  };

  const removeItem = (productId: string) => {
    setItems((current) =>
      current.filter((item) => item.product_id !== productId),
    );
  };

  const clear = () => setItems([]);

  const totalFor = (products: Product[]) =>
    items.reduce((total, item) => {
      const product = products.find(
        (candidate) => candidate.id === item.product_id,
      );
      if (!product) return total;
      const price =
        user?.membership_type === "member"
          ? product.member_price
          : product.nonmember_price;
      return total + price * item.quantity;
    }, 0);

  const value = useMemo<CartContextValue>(
    () => ({
      items,
      itemCount: items.reduce((total, item) => total + item.quantity, 0),
      addItem,
      setQuantity,
      removeItem,
      clear,
      totalFor,
    }),
    [items, user?.membership_type],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  const context = useContext(CartContext);
  if (!context) throw new Error("useCart 必須在 CartProvider 內使用");
  return context;
}
