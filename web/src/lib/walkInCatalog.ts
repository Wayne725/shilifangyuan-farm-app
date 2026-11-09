export interface WalkInCatalogSource {
  name: string;
  is_active: boolean;
  price?: number;
  nonmember_price?: number;
  tax_type?: "taxable" | "tax_exempt";
}

export function walkInCatalogItems(items: WalkInCatalogSource[], category: "meal" | "regular") {
  const names = new Set<string>();
  return items.filter(item => {
    if (!item.is_active || names.has(item.name)) return false;
    names.add(item.name);
    return true;
  }).map(item => {
    const price = category === "meal" ? item.price : item.nonmember_price;
    return {
      name: item.name,
      unitPrice: typeof price === "number" && Number.isSafeInteger(price) && price > 0 && price <= 1_000_000 ? String(price) : "",
      taxType: item.tax_type,
    };
  });
}
