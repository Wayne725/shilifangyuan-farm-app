import { router } from "../router";
import { captureSessionIdentityGuard } from "./api";

async function openProviderHandoff(url: string, navigate: () => Promise<unknown>, canLeave?: () => boolean): Promise<void> {
  const sameIdentity = captureSessionIdentityGuard();
  // Mount the return page before leaving, including browser history restoration.
  await navigate();
  if (!sameIdentity() || (canLeave && !canLeave())) throw new Error("登入身分已變更，請確認目前帳號後重新操作。");
  window.location.assign(url);
}

export async function openOrderHandoff(url: string, orderId: string, canLeave?: () => boolean): Promise<void> {
  await openProviderHandoff(url, () => router.navigate({ to: "/orders", search: { order_id: orderId }, replace: true }), canLeave);
}

export async function openMembershipHandoff(url: string, chargeId: string): Promise<void> {
  await openProviderHandoff(url, () => router.navigate({ to: "/account", search: { membership_charge_id: chargeId }, replace: true }));
}

export async function openActivityHandoff(url: string, chargeId: string, attemptId: string): Promise<void> {
  await openProviderHandoff(url, () => router.navigate({to: "/activities/registrations", search: {activity_charge_id: chargeId, attempt_id: attemptId, payment: "confirming"}, replace: true}));
}
