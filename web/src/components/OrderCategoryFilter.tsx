import { financialOrderCategoryLabels, orderCategoryLabels, type FinancialOrderCategory, type IssueOrderCategory } from "../lib/order-category";
import "./OrderCategoryFilter.css";

export function OrderCategoryFilter<T extends IssueOrderCategory | FinancialOrderCategory>({
  value, onChange, includeSystem = false, includeWalkInUnclassified = false,
}: {
  value: T; onChange: (value: T) => void; includeSystem?: boolean; includeWalkInUnclassified?: boolean;
}) {
  const labels = includeWalkInUnclassified ? financialOrderCategoryLabels : orderCategoryLabels;
  const options = includeSystem ? { ...labels, system: "其他系統工作" } : labels;
  return <label className="field order-category-filter">
    <span>訂單分類</span>
    <select value={value} onChange={(event) => onChange(event.target.value as T)}>
      {Object.entries(options).map(([category, label]) => <option key={category} value={category}>{label}</option>)}
    </select>
  </label>;
}
