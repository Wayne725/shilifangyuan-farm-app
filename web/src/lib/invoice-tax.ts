import type { InvoiceTax } from "./types";

export interface InvoiceTaxItem {
  tax_type?: string | null;
}

export function taxTypeLabel(taxType?: string | null): string {
  return { taxable: "應稅", tax_exempt: "免稅" }[taxType || ""] || "稅別未提供";
}

export function invoiceTaxSummary({
  items,
  memberTaxExempt,
  shippingFee = 0,
}: {
  items: ReadonlyArray<InvoiceTaxItem>;
  memberTaxExempt?: boolean;
  shippingFee?: number;
}): { label: string; reason: string | null } {
  const taxTypes = items.map((item) => item.tax_type);
  if (shippingFee > 0) taxTypes.push(memberTaxExempt === true ? "tax_exempt" : "taxable");
  const complete = taxTypes.length > 0 && taxTypes.every((type) => type === "taxable" || type === "tax_exempt");
  const label = complete
    ? new Set(taxTypes).size > 1 ? "應稅／免稅混合" : taxTypeLabel(taxTypes[0])
    : "稅別未提供";
  return { label, reason: memberTaxExempt === true ? "正式社員個人交易免稅，仍開立電子發票" : null };
}

export function resolveInvoiceTax({
  items = [],
  memberTaxExempt,
  shippingFee,
  taxAmount,
  invoiceTax,
  invoiceStatus,
  estimatedAmountLabel = "預估稅額",
}: {
  items?: ReadonlyArray<InvoiceTaxItem>;
  memberTaxExempt?: boolean;
  shippingFee?: number;
  taxAmount?: number;
  invoiceTax?: InvoiceTax | null;
  invoiceStatus?: string | null;
  estimatedAmountLabel?: string;
}): { taxTypeHeading: string; taxTypeLabel: string; amountLabel: string; amount: number | null; amountText: string | null } {
  const statuses = [invoiceStatus, invoiceTax?.status].filter((status): status is string => !!status);
  const issuedStates = ["issued", "void_pending", "voided"];
  const originalState = ["voided", "void_pending", "issued"].find((status) => statuses.includes(status));
  const unknownState = statuses.some((status) => ![...issuedStates, "not_eligible", "pending", "failed", "external"].includes(status));
  const validAmount = (amount?: number | null): amount is number => typeof amount === "number" && Number.isFinite(amount) && amount >= 0;
  if (originalState || unknownState) {
    const original = invoiceTax && issuedStates.includes(invoiceTax.status) ? invoiceTax : null;
    const voidLabel = originalState === "voided" ? "（已作廢）" : originalState === "void_pending" ? "（作廢處理中）" : "";
    const amount = original?.amount_semantics === "separately_stated" && validAmount(original.tax_amount) ? original.tax_amount : null;
    return {
      taxTypeHeading: voidLabel ? "原發票稅別" : "發票稅別",
      taxTypeLabel: original?.tax_type === "mixed" ? "應稅／免稅混合" : taxTypeLabel(original?.tax_type),
      amountLabel: voidLabel ? `原發票稅額${voidLabel}` : "發票稅額",
      amount,
      amountText: original?.amount_semantics === "included_not_separately_stated" ? "含稅，發票未分列稅額" : amount === null ? "發票稅額未提供" : null,
    };
  }
  return {
    taxTypeHeading: "預估稅別",
    taxTypeLabel: invoiceTaxSummary({ items, memberTaxExempt, shippingFee }).label,
    amountLabel: estimatedAmountLabel,
    amount: validAmount(taxAmount) ? taxAmount : null,
    amountText: validAmount(taxAmount) ? null : "預估稅額未提供",
  };
}
