import { router } from "../router";
import { captureSessionIdentityGuard } from "./api";
import { flushSync } from "react-dom";
import { beginOrderHandoff } from "./order-handoff";

async function openProviderHandoff(url: string, navigate: () => Promise<unknown>, canLeave?: () => boolean, returnHref?: string): Promise<void> {
  const sameIdentity = captureSessionIdentityGuard();
  let handoff: ReturnType<typeof beginOrderHandoff> | undefined;
  if (returnHref) flushSync(() => { handoff = beginOrderHandoff(returnHref); });
  try {
    // Keep the return page in history without starting its queries while leaving.
    await navigate();
    if (!sameIdentity() || (canLeave && !canLeave()) || (handoff && !handoff.isCurrent())) throw new Error("登入身分已變更，請確認目前帳號後重新操作。");
    window.location.assign(url);
  } catch (error) {
    handoff?.release();
    throw error;
  }
}

export async function openOrderHandoff(url: string, orderId: string, canLeave?: () => boolean): Promise<void> {
  const target = { to: "/orders" as const, search: { order_id: orderId }, replace: true };
  await openProviderHandoff(url, () => router.navigate(target), canLeave, router.buildLocation(target).href);
}

export async function openMembershipHandoff(url: string, chargeId: string): Promise<void> {
  await openProviderHandoff(url, () => router.navigate({ to: "/account", search: { membership_charge_id: chargeId }, replace: true }));
}

export async function openActivityHandoff(url: string, chargeId: string, attemptId: string): Promise<void> {
  await openProviderHandoff(url, () => router.navigate({to: "/activities/registrations", search: {activity_charge_id: chargeId, attempt_id: attemptId, payment: "confirming"}, replace: true}));
}
