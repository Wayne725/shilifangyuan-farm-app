import { Basket, Check, Plus } from "@phosphor-icons/react";
import { useEffect, useState } from "react";

import { useCommerce } from "../context/CommerceContext";
import type { Product } from "../lib/types";
import "./ProductAddButton.css";

export function ProductAddButton({ product, onAdd, compact = false }: {
  product: Product;
  onAdd: (product: Product) => void;
  compact?: boolean;
}) {
  const { cart } = useCommerce();
  const [addition, setAddition] = useState<{ productId: string; sequence: number } | null>(null);
  const added = addition?.productId === product.id;
  const quantity = cart.find((item) => item.product.id === product.id)?.quantity || 0;

  useEffect(() => {
    if (!addition) return;
    const timer = window.setTimeout(() => setAddition(null), 3000);
    return () => window.clearTimeout(timer);
  }, [addition]);

  return <>
    <button
      className={compact ? "icon-button add-button" : "button button-primary full-width"}
      type="button"
      disabled={product.stock_quantity <= 0}
      aria-label={compact ? `加入 ${product.name}` : undefined}
      onClick={() => {
        onAdd(product);
        setAddition((current) => ({ productId: product.id, sequence: (current?.sequence || 0) + 1 }));
      }}
    >
      {added ? <Check size={compact ? 20 : 18} aria-hidden="true" /> : compact ? <Plus size={20} weight="light" aria-hidden="true" /> : <Basket size={18} aria-hidden="true" />}
      {!compact && (product.stock_quantity > 0 ? "加入結帳清單" : "目前缺貨")}
    </button>
    <p className="cart-add-feedback" role="status" aria-live="polite" aria-atomic="true">
      {added && quantity > 0 ? `${product.name}已加入，清單共 ${quantity} ${product.unit}` : ""}
    </p>
  </>;
}
