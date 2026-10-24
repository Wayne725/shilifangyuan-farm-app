import { orderCategoryLabels, type IssueOrderCategory } from "../lib/order-category";
import "./OrderCategoryFilter.css";

export function OrderCategoryFilter<T extends IssueOrderCategory>({ value, onChange, includeSystem = false }: {
  value: T; onChange: (value: T) => void; includeSystem?: boolean;
}) {
  const options = includeSystem ? { ...orderCategoryLabels, system: "其他系統工作" } : orderCategoryLabels;
  return <label className="field order-category-filter">
    <span>訂單分類</span>
    <select value={value} onChange={(event) => onChange(event.target.value as T)}>
      {Object.entries(options).map(([category, label]) => <option key={category} value={category}>{label}</option>)}
    </select>
  </label>;
}
