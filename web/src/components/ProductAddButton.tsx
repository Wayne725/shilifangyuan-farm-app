import { Basket, Check } from "@phosphor-icons/react";
import { useEffect, useId, useState } from "react";

import { useCommerce } from "../context/CommerceContext";
import type { Product } from "../lib/types";
import { productQuantityLimit } from "../lib/cart-quantity";
import "./ProductAddButton.css";

export function ProductAddButton({ product, onAdd, compact = false }: {
  product: Product;
  onAdd: (product: Product, quantity?: number) => void;
  compact?: boolean;
}) {
  const { cart } = useCommerce();
  const errorId = useId();
  const [choice, setChoice] = useState("1");
  const [customQuantity, setCustomQuantity] = useState("");
  const [error, setError] = useState("");
  const [addition, setAddition] = useState<{ productId: string; sequence: number } | null>(null);
  const added = addition?.productId === product.id;
  const quantity = cart.find((item) => item.product.id === product.id)?.quantity || 0;
  const limit = productQuantityLimit(product);
  const remaining = Math.max(0, limit - quantity);
  const requested = Number(choice === "custom" ? customQuantity : choice);
  const valid = Number.isInteger(requested) && requested >= 1 && requested <= remaining;
  const commonMaximum = Math.min(remaining, 10);
  const blocking = remaining === 0
    ? product.stock_quantity <= 0 ? "目前缺貨" : `清單已有 ${quantity} ${product.unit}，目前可購上限為 ${limit} ${product.unit}`
    : !valid && (choice !== "custom" || customQuantity !== "") ? `請選擇 1 至 ${remaining} ${product.unit}` : "";
  const message = error || blocking;

  useEffect(() => {
    setChoice("1"); setCustomQuantity(""); setError(""); setAddition(null);
  }, [product.id]);

  useEffect(() => {
    if (!addition) return;
    const timer = window.setTimeout(() => setAddition(null), 3000);
    return () => window.clearTimeout(timer);
  }, [addition]);

  return <div className={`product-add-control${compact ? " product-add-control--compact" : ""}`}>
    <div className="product-add-quantity">
      {choice !== "custom" && <label><span>數量</span>
        <select aria-label={`${product.name}加入數量`} value={remaining ? choice : "0"} disabled={!remaining}
          aria-describedby={message ? errorId : undefined}
          onChange={(event) => { setChoice(event.target.value); setError(""); setAddition(null); }}>
          {!remaining && <option value="0">{product.stock_quantity <= 0 ? "缺貨" : "已達上限"}</option>}
          {Array.from({ length: commonMaximum }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1} {product.unit}</option>)}
          {choice !== "custom" && Number(choice) > commonMaximum && remaining > 0 && <option value={choice} disabled>{choice} {product.unit}（超過可加數量）</option>}
          {(remaining > 10 || choice === "custom") && <option value="custom">其他數量</option>}
        </select>
      </label>}
      {choice === "custom" && remaining > 0 && <label><span>自訂數量</span>
        <input type="number" inputMode="numeric" min={1} max={remaining} step={1} required
          aria-label={`${product.name}自訂數量`} aria-invalid={customQuantity !== "" && !valid}
          aria-describedby={message ? errorId : undefined} value={customQuantity}
          onChange={(event) => { setCustomQuantity(event.target.value); setError(""); setAddition(null); }} />
      </label>}
      {choice === "custom" && <button className="product-quantity-reset" type="button" onClick={() => { setChoice("1"); setCustomQuantity(""); setError(""); setAddition(null); }}>改用常用數量</button>}
    </div>
    <button
      className="button button-primary product-add-submit"
      type="button"
      disabled={!valid}
      aria-describedby={message ? errorId : undefined}
      aria-label={`加入購物車：${product.name}`}
      onClick={() => {
        setError("");
        try {
          onAdd(product, requested);
          setAddition((current) => ({ productId: product.id, sequence: (current?.sequence || 0) + 1 }));
        } catch (reason) {
          setAddition(null);
          setError(reason instanceof RangeError ? reason.message : "無法儲存結帳清單，請再試一次。");
        }
      }}
    >
      {added ? <Check size={18} aria-hidden="true" /> : <Basket size={18} aria-hidden="true" />}
      {product.stock_quantity <= 0 ? "目前缺貨" : remaining ? "加入購物車" : "已達可購數量"}
    </button>
    <p className="cart-add-feedback" role="status" aria-live="polite" aria-atomic="true">
      {added && quantity > 0 ? `${product.name}已加入，清單共 ${quantity} ${product.unit}` : ""}
    </p>
    {message && <p className="product-add-error" id={errorId} role="alert">{message}</p>}
  </div>;
}
