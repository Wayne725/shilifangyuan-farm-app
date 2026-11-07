import { type ReactNode, useSyncExternalStore } from "react";
import { getOrderHandoff, subscribeOrderHandoff } from "../lib/order-handoff";

export function OrderHandoffBoundary({ children }: { children: ReactNode }) {
  const handoff = useSyncExternalStore(subscribeOrderHandoff, getOrderHandoff, () => null);
  if (!handoff) return children;
  return (
    <section className="account-gate" aria-label="交易頁面交接">
      <h1 role="status">正在開啟交易頁面…</h1>
      <a className="button button-quiet" href={handoff.returnHref}>返回訂單</a>
    </section>
  );
}
