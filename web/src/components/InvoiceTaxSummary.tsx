import { Receipt } from "@phosphor-icons/react";

import { formatMoney } from "../lib/api";
import { invoiceTaxSummary, type InvoiceTaxItem } from "../lib/invoice-tax";

interface InvoiceTaxSummaryProps {
  items: ReadonlyArray<InvoiceTaxItem>;
  memberTaxExempt?: boolean;
  shippingFee?: number;
  taxAmount?: number;
  taxAmountLabel?: string;
  variant?: "rows" | "card";
}

export function InvoiceTaxSummary({
  items,
  memberTaxExempt,
  shippingFee,
  taxAmount,
  taxAmountLabel = "稅額",
  variant = "rows",
}: InvoiceTaxSummaryProps) {
  const summary = invoiceTaxSummary({ items, memberTaxExempt, shippingFee });
  const amount = typeof taxAmount === "number" && !(memberTaxExempt && taxAmount === 0) && <div><dt>{taxAmountLabel}</dt><dd>{formatMoney(taxAmount)}</dd></div>;
  if (variant === "card") {
    return <section className="invoice-summary-card" aria-label="訂單稅別">
      <Receipt size={25} weight="light" />
      <div><small>發票稅別</small><strong>{summary.label}</strong></div>
      <dl>{amount}</dl>
    </section>;
  }
  return <>
    <div><dt>發票稅別</dt><dd>{summary.label}</dd></div>
    {amount}
  </>;
}
