import { Receipt } from "@phosphor-icons/react";

import { formatMoney } from "../lib/api";
import { resolveInvoiceTax, type InvoiceTaxItem } from "../lib/invoice-tax";
import type { InvoiceTax } from "../lib/types";

interface InvoiceTaxSummaryProps {
  items: ReadonlyArray<InvoiceTaxItem>;
  memberTaxExempt?: boolean;
  shippingFee?: number;
  taxAmount?: number;
  invoiceTax?: InvoiceTax | null;
  invoiceStatus?: string | null;
  taxAmountLabel?: string;
  variant?: "rows" | "card";
}

export function InvoiceTaxSummary({
  items,
  memberTaxExempt,
  shippingFee,
  taxAmount,
  invoiceTax,
  invoiceStatus,
  taxAmountLabel = "預估稅額",
  variant = "rows",
}: InvoiceTaxSummaryProps) {
  const summary = resolveInvoiceTax({ items, memberTaxExempt, shippingFee, taxAmount, invoiceTax, invoiceStatus, estimatedAmountLabel: taxAmountLabel });
  const amount = <div><dt>{summary.amountLabel}</dt><dd>{summary.amount === null ? summary.amountText : formatMoney(summary.amount)}</dd></div>;
  if (variant === "card") {
    return <section className="invoice-summary-card" aria-label="訂單稅別">
      <Receipt size={25} weight="light" />
      <div><small>{summary.taxTypeHeading}</small><strong>{summary.taxTypeLabel}</strong></div>
      <dl>{amount}</dl>
    </section>;
  }
  return <>
    <div><dt>{summary.taxTypeHeading}</dt><dd>{summary.taxTypeLabel}</dd></div>
    {amount}
  </>;
}
