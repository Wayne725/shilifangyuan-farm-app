interface CartQuantity { product: { id: string }; quantity: number }
interface SubmittedQuantity { product_id: string; quantity: number }

export function cartMatchesSubmission(cart: CartQuantity[], submitted: SubmittedQuantity[]): boolean {
  const quantities = (items: SubmittedQuantity[]) => {
    const totals = new Map<string, number>();
    for (const item of items) totals.set(item.product_id, (totals.get(item.product_id) ?? 0) + item.quantity);
    return [...totals].sort(([left], [right]) => left.localeCompare(right));
  };
  return JSON.stringify(quantities(cart.map(item => ({ product_id: item.product.id, quantity: item.quantity }))))
    === JSON.stringify(quantities(submitted));
}
