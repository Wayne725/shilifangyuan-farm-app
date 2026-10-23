import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { apiFetch } from "../lib/api";
import type { Meal } from "../lib/types";
import { MealPhoto } from "./MealPhoto";

export function MealImageEditor({ meal, onDone }: { meal: Meal; onDone: () => void }) {
  return <details className="meal-image-editor">
    <summary>上傳照片</summary>
    <ImageField mealId={meal.id} label={`${meal.name}照片`} current={meal.image_url} onDone={onDone} />
    {meal.option_groups.flatMap((group) => group.options.map((option) => <ImageField key={option.id} mealId={meal.id} optionId={option.id} label={`${group.name} · ${option.name}照片`} current={option.image_url} onDone={onDone} />))}
  </details>;
}

function ImageField({ mealId, optionId, label, current, onDone }: {
  mealId: string; optionId?: string; label: string; current?: string | null; onDone: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string>();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!file) { setPreview(undefined); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const path = `/v1/admin/meals/${mealId}/photo${optionId ? `?option_id=${encodeURIComponent(optionId)}` : ""}`;
  const save = useMutation({
    mutationFn: (remove: boolean) => {
      if (remove) return apiFetch<Meal>(path, { method: "DELETE" });
      if (!file) throw new Error("請選擇照片");
      if (file.size > 5 * 1024 * 1024) throw new Error("照片不可超過 5 MB");
      return apiFetch<Meal>(path, { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } });
    },
    onSuccess: () => { setFile(null); if (input.current) input.current.value = ""; onDone(); },
  });
  return <form className="meal-photo-upload" onSubmit={(event) => { event.preventDefault(); save.mutate(false); }}>
    <div className="meal-image-field"><MealPhoto src={preview || current} alt={label} />
      <label className="field"><span>{label}（JPG／PNG／WebP，5 MB 以內）</span><input ref={input} type="file" accept="image/jpeg,image/png,image/webp" disabled={save.isPending} onChange={(event) => { setFile(event.target.files?.[0] || null); save.reset(); }} /></label>
    </div>
    {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
    {save.isSuccess && !file && <p role="status">照片已更新</p>}
    <div className="form-actions"><button className="button button-quiet" disabled={!file || save.isPending}>{save.isPending ? "處理中…" : "上傳照片"}</button>{current && <button className="button button-quiet" type="button" disabled={save.isPending} onClick={() => save.mutate(true)}>移除照片</button>}</div>
  </form>;
}
