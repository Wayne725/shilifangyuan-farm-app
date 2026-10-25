import type { Product } from "./types";

export const maxOrderItemQuantity = 999;

export function productQuantityLimit(product: Pick<Product, "stock_quantity">): number {
  return Math.max(0, Math.min(maxOrderItemQuantity, Math.floor(product.stock_quantity)));
}
