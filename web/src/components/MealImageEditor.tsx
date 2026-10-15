import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch } from "../lib/api";
import type { Meal } from "../lib/types";
import { MealPhoto } from "./MealPhoto";

export function MealImageEditor({ meal, onDone }: { meal: Meal; onDone: () => void }) {
  const [imageUrl, setImageUrl] = useState(meal.image_url || "");
  const options = meal.option_groups.flatMap((group) => group.options.map((option) => ({ ...option, groupName: group.name })));
  const [optionImages, setOptionImages] = useState<Record<string, string>>(() => Object.fromEntries(options.map((option) => [option.id, option.image_url || ""])));
  const mainChanged = imageUrl.trim() !== (meal.image_url || "");
  const changedOptions = options.filter((option) => (optionImages[option.id] || "").trim() !== (option.image_url || ""));
  const save = useMutation({
    mutationFn: () => apiFetch<Meal>(`/v1/admin/meals/${meal.id}/images`, {
      method: "PATCH",
      body: JSON.stringify({
        ...(mainChanged ? { image_url: imageUrl.trim() || null } : {}),
        options: changedOptions.map((option) => ({ id: option.id, image_url: optionImages[option.id].trim() || null })),
      }),
    }),
    onSuccess: onDone,
  });
  return <details className="meal-image-editor">
    <summary>照片設定</summary>
    <form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
      <ImageField label={`${meal.name}照片網址`} value={imageUrl} onChange={setImageUrl} />
      {options.map((option) => <ImageField key={option.id} label={`${option.groupName} · ${option.name}照片網址`} value={optionImages[option.id] || ""} onChange={(value) => setOptionImages((current) => ({ ...current, [option.id]: value }))} />)}
      {save.isError && <p className="form-error">{save.error.message}</p>}
      {save.isSuccess && !mainChanged && !changedOptions.length && <p role="status">照片已儲存</p>}
      <button className="button button-quiet" disabled={save.isPending || (!mainChanged && !changedOptions.length)}>{save.isPending ? "儲存中…" : "儲存照片"}</button>
    </form>
  </details>;
}

function ImageField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <div className="meal-image-field">
    <MealPhoto src={value.trim()} alt={label.replace("網址", "")} />
    <label className="field"><span>{label}</span><input type="url" maxLength={500} value={value} placeholder="https://…" onChange={(event) => onChange(event.target.value)} /></label>
  </div>;
}
