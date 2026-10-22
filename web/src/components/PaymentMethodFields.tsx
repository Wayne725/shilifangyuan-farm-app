import { Money, CreditCard } from "@phosphor-icons/react";

export function PaymentMethodFields({ value, onChange, allowCash, disabled = false }: {
  value: "online" | "cash_on_pickup";
  onChange: (value: "online" | "cash_on_pickup") => void;
  allowCash: boolean;
  disabled?: boolean;
}) {
  return <fieldset className="payment-method-fields" disabled={disabled}>
    <legend className="checkout-step-title"><div><small>PAYMENT / 付款</small><h2>付款方式</h2></div></legend>
    <div className="payment-method-options">
      <label className={value === "online" ? "selected" : ""}><input type="radio" name="payment_method" checked={value === "online"} onChange={() => onChange("online")} /><CreditCard size={22} /><span>線上付款</span></label>
      {allowCash && <label className={value === "cash_on_pickup" ? "selected" : ""}><input type="radio" name="payment_method" checked={value === "cash_on_pickup"} onChange={() => onChange("cash_on_pickup")} /><Money size={22} /><span>合作社現場付款</span></label>}
    </div>
  </fieldset>;
}
