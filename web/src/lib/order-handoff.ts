import { subscribeSession } from "./api";

type OrderHandoff = { returnHref: string };

let current: OrderHandoff | null = null;
const listeners = new Set<() => void>();

export const getOrderHandoff = () => current;
export const subscribeOrderHandoff = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

export function beginOrderHandoff(returnHref: string) {
  if (current) throw new Error("正在交接付款頁，請先返回訂單再試。");
  const handoff = { returnHref };
  const release = () => {
    window.removeEventListener("pageshow", release);
    unsubscribeSession();
    if (current !== handoff) return;
    current = null;
    for (const listener of listeners) listener();
  };
  const unsubscribeSession = subscribeSession((_user, identityChanged) => {
    if (identityChanged) release();
  });
  window.addEventListener("pageshow", release);
  current = handoff;
  for (const listener of listeners) listener();
  return { release, isCurrent: () => current === handoff };
}
