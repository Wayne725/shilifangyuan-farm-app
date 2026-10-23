import * as Dialog from "@radix-ui/react-dialog";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch } from "../lib/api";
import type { Meal, MealOptionGroup } from "../lib/types";

type OptionDraft = { id?: string; name: string; price_delta: number; is_active: boolean };
type GroupDraft = { id?: string; name: string; min_selections: number; max_selections: number; is_active: boolean; options: OptionDraft[] };
const groupDraft = (group: MealOptionGroup): GroupDraft => ({
  id: group.id, name: group.name, min_selections: group.min_selections, max_selections: group.max_selections,
  is_active: group.is_active, options: group.options.map(({ id, name, price_delta, is_active }) => ({ id, name, price_delta, is_active })),
});

export function MealOptionsEditor({ meal, onDone }: { meal: Meal; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  return <Dialog.Root open={open} onOpenChange={(value) => { setOpen(value); if (value) setSaved(false); }}>
    <Dialog.Trigger className="button button-quiet">編輯主食與加購選項</Dialog.Trigger>
    {saved && <p role="status">選項已儲存</p>}
    <Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="meal-options-dialog" aria-describedby={undefined}>
      <div className="meal-options-dialog-heading"><Dialog.Title>{meal.name} · 選項設定</Dialog.Title><Dialog.Close className="button button-quiet" aria-label="關閉選項設定">×</Dialog.Close></div>
      <MealOptionsForm meal={meal} onDone={() => { setOpen(false); setSaved(true); onDone(); }} />
      <Dialog.Close className="button button-quiet">關閉</Dialog.Close>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}

function MealOptionsForm({ meal, onDone }: { meal: Meal; onDone: () => void }) {
  const [groups, setGroups] = useState<GroupDraft[]>(() => meal.option_groups.map(groupDraft));
  const [version] = useState(meal.updated_at);
  const change = (index: number, patch: Partial<GroupDraft>) => setGroups((current) => current.map((group, i) => i === index ? { ...group, ...patch } : group));
  const save = useMutation({
    mutationFn: () => apiFetch<Meal>(`/v1/admin/meals/${meal.id}/options`, { method: "PUT", body: JSON.stringify({ expected_updated_at: version, option_groups: groups }) }),
    onSuccess: onDone,
  });
  return <form className="meal-options-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
    <fieldset disabled={save.isPending} className="meal-options-fields">
    {groups.map((group, index) => <fieldset key={index} className="meal-option-editor-group">
      <legend>選項群組 {index + 1}</legend>
      <div className="field-grid two-columns"><label className="field"><span>群組名稱</span><input required maxLength={120} value={group.name} onChange={(event) => change(index, { name: event.target.value })} /></label>
        <label className="checkbox-field"><input type="checkbox" checked={group.is_active} onChange={(event) => change(index, { is_active: event.target.checked })} />啟用群組</label>
        <label className="field"><span>必選項數</span><input required type="number" min={0} max={20} value={group.min_selections} onChange={(event) => change(index, { min_selections: Number(event.target.value) })} /></label>
        <label className="field"><span>最多選幾項</span><input required type="number" min={1} max={20} value={group.max_selections} onChange={(event) => change(index, { max_selections: Number(event.target.value) })} /></label>
      </div>
      {group.options.map((option, optionIndex) => <div className="meal-option-editor-row" key={optionIndex}>
        <label className="field"><span>選項名稱</span><input required maxLength={120} value={option.name} onChange={(event) => change(index, { options: group.options.map((item, i) => i === optionIndex ? { ...item, name: event.target.value } : item) })} /></label>
        <label className="field"><span>加價（元）</span><input required type="number" min={0} max={100000} value={option.price_delta} onChange={(event) => change(index, { options: group.options.map((item, i) => i === optionIndex ? { ...item, price_delta: Number(event.target.value) } : item) })} /></label>
        <label className="checkbox-field"><input type="checkbox" checked={option.is_active} onChange={(event) => change(index, { options: group.options.map((item, i) => i === optionIndex ? { ...item, is_active: event.target.checked } : item) })} />啟用</label>
        {!option.id && <button type="button" className="button button-quiet" onClick={() => change(index, { options: group.options.filter((_, i) => i !== optionIndex) })}>移除新選項</button>}
      </div>)}
      <button className="button button-quiet" type="button" disabled={group.options.length >= 50} onClick={() => change(index, { options: [...group.options, { name: "", price_delta: 0, is_active: true }] })}>新增選項</button>
      {!group.id && <button className="button button-quiet" type="button" onClick={() => setGroups(groups.filter((_, i) => i !== index))}>移除新群組</button>}
    </fieldset>)}
    <button className="button button-quiet" type="button" disabled={groups.length >= 20} onClick={() => setGroups([...groups, { name: "", min_selections: 0, max_selections: 1, is_active: true, options: [{ name: "", price_delta: 0, is_active: true }] }])}>新增選項群組</button>
    </fieldset>
    {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
    <button className="button button-primary" disabled={save.isPending}>{save.isPending ? "儲存中…" : "儲存選項"}</button>
  </form>;
}
