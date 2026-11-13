import { formatMoney } from "../lib/api";
import { businessMealPaymentState, type BusinessMealPayment } from "../lib/business-meals";
import { formatMealDateTime } from "../lib/meal-time";
import { useDeadlineClock } from "../lib/use-deadline-clock";

export function BusinessMealPaymentStatus({ order, onPay, paying }: {
  order: BusinessMealPayment & { amount_total: number };
  onPay: () => void;
  paying: boolean;
}) {
  const deadline = order.payment_opens_at && Date.now() < Date.parse(order.payment_opens_at) ? order.payment_opens_at : order.payment_closes_at;
  const now = useDeadlineClock(deadline);
  const state = businessMealPaymentState(order, now);
  if (order.meal_booking_type === "business" && order.payment_method === "cash_on_pickup") {
    if (order.cancelled_at) return null;
    if (order.payment_status === "paid") return <p>現金付款・已付款</p>;
    if (!state) return null;
    return <section className="credential-pending business-meal-payment" aria-label="會餐付款安排">
      <h3>{state === "overdue" ? "現金付款・已逾期" : "現金付款・待取餐時付款"}</h3>
      <p>{state === "overdue" ? "請聯絡合作社處理此筆未付款會餐。" : `取餐時應付 ${formatMoney(order.amount_total)}`}</p>
    </section>;
  }
  if (!state) return null;
  return <section className="credential-pending business-meal-payment" aria-label="會餐付款安排">
    <h3>{state === "not_open" ? "會餐預訂成功" : state === "overdue" ? "會餐付款已逾期" : state === "open" ? "會餐待付款" : "付款資格確認中"}</h3>
    <p>{state === "not_open" ? `取餐當日 00:00 起可付款（${formatMealDateTime(order.payment_opens_at!)}）`
      : state === "overdue" ? "請聯絡合作社處理此筆未付款會餐。"
      : state === "open" ? "請於取餐當日完成付款，發票於付款確認後開立。"
      : "請更新訂單以確認付款資格。"}</p>
    {state !== "overdue" && <button className="button button-primary" type="button" disabled={paying || state !== "open"} onClick={() => { if (businessMealPaymentState(order) === "open") onPay(); }}>{paying ? "準備付款頁…" : `前往付款 ${formatMoney(order.amount_total)}`}</button>}
  </section>;
}
