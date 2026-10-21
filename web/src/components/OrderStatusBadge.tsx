import { CheckCircle, Clock, MinusCircle, WarningCircle } from "@phosphor-icons/react";

import { adminPaymentStatusLabel, fulfillmentStatusLabel, invoiceStatusLabel } from "../lib/labels";

type StatusKind = "payment" | "fulfillment" | "invoice";
type StatusTone = "attention" | "progress" | "complete" | "exception" | "neutral";

const tones: Record<StatusKind, Record<string, StatusTone>> = {
  payment: {
    pending: "attention", paid: "complete", refund_pending: "progress",
    late_paid_refund_required: "attention", failed: "exception", refunded: "neutral", expired: "neutral",
  },
  fulfillment: {
    pending: "attention", pending_confirmation: "attention", awaiting_shipment: "attention",
    preparing: "progress", shipped: "progress", ready: "attention", ready_for_pickup: "attention",
    picked_up: "complete", delivered: "complete", cancelled: "neutral", no_show: "exception", exception: "exception",
  },
  invoice: {
    not_eligible: "neutral", pending: "progress", issued: "complete", failed: "exception",
    void_pending: "progress", voided: "neutral",
  },
};
const labels = { payment: adminPaymentStatusLabel, fulfillment: fulfillmentStatusLabel, invoice: invoiceStatusLabel };
const kindLabels = { payment: "付款", fulfillment: "履約", invoice: "發票" };
const indicators = { attention: Clock, progress: Clock, complete: CheckCircle, exception: WarningCircle, neutral: MinusCircle };

export function OrderStatusBadge({ kind, status, label }: { kind: StatusKind; status: string; label?: string }) {
  const tone = tones[kind][status] || "neutral";
  const Indicator = indicators[tone];
  const text = label || labels[kind](status);
  return <span className={`order-status-badge status-${tone}`} aria-label={`${kindLabels[kind]}：${text}`}>
    <Indicator size={17} aria-hidden="true" />{text}
  </span>;
}
