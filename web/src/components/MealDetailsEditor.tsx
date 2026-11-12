import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch } from "../lib/api";
import type { Meal } from "../lib/types";

export function MealDetailsEditor({ meal, onDone }: { meal: Meal; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  return <>
    <button className="button button-quiet" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? "收起餐點資料" : "編輯餐點"}
    </button>
    {open && <MealDetailsForm meal={meal} onDone={() => { setOpen(false); onDone(); }} />}
  </>;
}

function MealDetailsForm({ meal, onDone }: { meal: Meal; onDone: () => void }) {
  const [draft, setDraft] = useState(() => ({
    name: meal.name, description: meal.description, price: meal.price, tax_type: meal.tax_type, is_active: meal.is_active,
  }));
  const [original] = useState(meal);
  const [confirmed, setConfirmed] = useState(false);
  const requiresImpact = draft.price !== original.price || draft.tax_type !== original.tax_type || (original.is_active && !draft.is_active);
  const impact = useQuery({ queryKey: ["meal-edit-impact", meal.id], enabled: requiresImpact, retry: false, refetchOnWindowFocus: false,
    queryFn: ({ signal }) => apiFetch<Array<{ id: string; title: string }>>(`/v1/admin/meals/${meal.id}/edit-impact`, { signal }) });
  const affected = requiresImpact ? (impact.data ?? []) : [];
  const change = <K extends keyof typeof draft>(key: K, value: typeof draft[K]) => {
    setDraft(current => ({ ...current, [key]: value }));
    if (key === "price" || key === "tax_type" || key === "is_active") setConfirmed(false);
  };
  const save = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/meals/${meal.id}`, { method: "PATCH", body: JSON.stringify({
      ...draft, expected_updated_at: original.updated_at, confirmed_event_ids: confirmed ? affected.map(event => event.id) : [],
    }) }),
    onSuccess: onDone,
  });
  const error = (requiresImpact ? impact.error : null) || save.error;
  const canSave = (!requiresImpact || (impact.isSuccess && !impact.isFetching && (!affected.length || confirmed))) && !save.isPending;
  return <form className="social-form" aria-label="編輯餐點" onSubmit={event => {
    event.preventDefault();
    if (canSave) save.mutate();
  }}>
    <label className="field"><span>餐點名稱</span><input required maxLength={120} value={draft.name} onChange={event => change("name", event.target.value)} /></label>
    <label className="field"><span>說明</span><textarea maxLength={5000} value={draft.description} onChange={event => change("description", event.target.value)} /></label>
    <label className="field"><span>售價</span><input type="number" required min={0} max={1000000} value={draft.price} onChange={event => change("price", event.target.valueAsNumber)} /></label>
    <label className="field"><span>稅別</span><select value={draft.tax_type} onChange={event => change("tax_type", event.target.value === "tax_exempt" ? "tax_exempt" : "taxable")}><option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label>
    <label className="field"><span>販售狀態</span><select value={draft.is_active ? "active" : "inactive"} onChange={event => change("is_active", event.target.value === "active")}><option value="active">開放</option><option value="inactive">停售</option></select></label>
    {affected.length > 0 && <fieldset><legend>已發布場次</legend><ul>{affected.map(event => <li key={event.id}>{event.title}</li>)}</ul>
      <label className="check-field"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />確認影響上述場次的新訂單</label>
    </fieldset>}
    {error && <p className="form-error" role="alert">{error.message}</p>}
    {requiresImpact && (impact.isError || save.isError) && <button type="button" className="button button-quiet" onClick={() => { setConfirmed(false); void impact.refetch(); }}>重新讀取場次</button>}
    <button className="button button-system" disabled={!canSave}>儲存餐點</button>
  </form>;
}
