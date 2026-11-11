export interface WalkInCatalogSource {
  id: string;
  name: string;
  is_active: boolean;
  sku?: string | null;
  product_number?: string | null;
  slug?: string | null;
  price?: number;
  member_price?: number;
  nonmember_price?: number;
  tax_type?: "taxable" | "tax_exempt";
}

export interface WalkInCatalogItem {
  id: string;
  name: string;
  label: string;
  unitPrice: string;
  memberPrice?: number;
  taxType?: WalkInCatalogSource["tax_type"];
}

export function walkInCatalogItems(items: WalkInCatalogSource[], category: "meal" | "regular"): WalkInCatalogItem[] {
  const activeItems = items.filter(item => item.is_active);
  const nameCounts = new Map<string, number>();
  for (const item of activeItems) {
    const name = item.name.trim();
    nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);
  }
  return activeItems.map(item => {
    const name = item.name.trim();
    const price = category === "meal" ? item.price : item.nonmember_price;
    return {
      id: item.id,
      name,
      label: (nameCounts.get(name) ?? 0) > 1 ? `${name}（${item.sku || item.product_number || item.slug || item.id}）` : name,
      unitPrice: typeof price === "number" && Number.isSafeInteger(price) && price > 0 && price <= 1_000_000 ? String(price) : "",
      memberPrice: category === "regular" ? item.member_price : undefined,
      taxType: item.tax_type,
    };
  });
}
