import { orderCategoryLabels, type IssueOrderCategory } from "../lib/order-category";
import "./OrderCategoryFilter.css";

export function OrderCategoryFilter<T extends IssueOrderCategory>({ value, onChange, includeSystem = false }: {
  value: T; onChange: (value: T) => void; includeSystem?: boolean;
}) {
  const options = includeSystem ? { ...orderCategoryLabels, system: "其他系統工作" } : orderCategoryLabels;
  return <div className="order-category-filter" role="group" aria-label="訂單分類">
    {Object.entries(options).map(([category, label]) => <button className="button button-quiet" type="button" key={category} aria-pressed={value === category} onClick={() => onChange(category as T)}>{label}</button>)}
  </div>;
}
