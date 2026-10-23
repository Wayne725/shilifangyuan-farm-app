import { useSearch } from "@tanstack/react-router";
import { MealOrderHistory } from "../components/MealOrderHistory";

export function MealOrdersPage() {
  const search = useSearch({ from: "/meal-orders" });
  return <MealOrderHistory orderId={search.order_id} />;
}
