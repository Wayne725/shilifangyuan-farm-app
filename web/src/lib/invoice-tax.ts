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
