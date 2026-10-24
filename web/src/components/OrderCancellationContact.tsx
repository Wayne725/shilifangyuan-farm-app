import { Phone } from "@phosphor-icons/react";

import { useStorefrontStatus } from "./StorefrontStatus";
import "./OrderCancellation.css";

export function OrderCancellationContact() {
  const platform = useStorefrontStatus();
  const phone = platform.data?.support_phone?.trim() || "";
  const dialNumber = phone.replace(/[\s()-]/g, "");
  const canCall = !platform.isError && /^\+?\d{7,15}$/.test(dialNumber);

  return <section className="order-cancellation-contact" aria-label="電話聯絡退單">
    {canCall ? <a className="button button-quiet" href={`tel:${dialNumber}`}><Phone size={18} />電話聯絡退單 · {phone}</a>
      : <button className="button button-quiet" type="button" disabled><Phone size={18} />{platform.isPending ? "載入退單聯絡電話…" : platform.isError ? "退單聯絡電話暫時無法讀取" : "退單聯絡電話尚未設定"}</button>}
    {platform.isError && <button className="text-link" type="button" onClick={() => void platform.refetch()}>重新讀取聯絡電話</button>}
  </section>;
}
