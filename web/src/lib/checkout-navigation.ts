import { router } from "../router";

export async function openOrderHandoff(url: string, orderId: string): Promise<void> {
  // Mount the return page before leaving, including browser history restoration.
  await router.navigate({ to: "/orders", search: { order_id: orderId }, replace: true });
  window.location.assign(url);
}

export async function openMembershipHandoff(url: string, chargeId: string): Promise<void> {
  await router.navigate({ to: "/account", search: { membership_charge_id: chargeId }, replace: true });
  window.location.assign(url);
}
