import { useEffect, useState } from "react";

import { apiFetch, fetchApiBlob, resolveAsset } from "../lib/api";

export function PhotoImage({ src, alt }: { src?: string | null; alt: string }) {
  const [loaded, setLoaded] = useState<{ src: string; url: string }>();
  const privatePhoto = Boolean(src?.startsWith("/v1/member-photos/"));
  useEffect(() => {
    if (!privatePhoto || !src) return;
    let active = true;
    let url: string | undefined;
    const controller = new AbortController();
    void fetchApiBlob(src, controller.signal).then((blob) => {
      if (!active) return;
      url = URL.createObjectURL(blob);
      setLoaded({ src, url });
    }).catch(() => { if (active) setLoaded(undefined); });
    return () => { active = false; controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [src, privatePhoto]);
  const image = privatePhoto ? loaded && loaded.src === src ? loaded.url : undefined : src ? resolveAsset(src) : undefined;
  return image ? <img src={image} alt={alt} /> : <span className="photo-placeholder" aria-label={alt}>尚無照片</span>;
}

export function PhotoPicker({ label, current, file, remove, disabled, onChange }: {
  label: string; current?: string | null; file: File | null; remove: boolean; disabled?: boolean;
  onChange: (file: File | null, remove: boolean) => void;
}) {
  const [preview, setPreview] = useState<string>();
  const [error, setError] = useState("");
  const [inputKey, setInputKey] = useState(0);
  useEffect(() => {
    if (!file) { setPreview(undefined); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return <div className="photo-picker field wide">
    <div className="photo-picker-preview"><PhotoImage src={preview || (remove ? null : current)} alt={`${label}預覽`} /></div>
    <label className="field"><span>{label}（JPG／PNG／WebP，5 MB 以內）</span>
      <input key={inputKey} type="file" accept="image/jpeg,image/png,image/webp" disabled={disabled} onChange={(event) => {
        const selected = event.target.files?.[0] || null;
        const invalid = selected && (selected.size > 5 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(selected.type));
        setError(invalid ? "請選擇 5 MB 以內的 JPG、PNG 或 WebP 照片" : "");
        if (invalid) { event.target.value = ""; return; }
        onChange(selected, false);
      }} />
    </label>
    {error && <p className="form-error" role="alert">{error}</p>}
    {(file || current) && <button className="button button-quiet" disabled={disabled} type="button" onClick={() => { onChange(null, !remove); setError(""); setInputKey((value) => value + 1); }}>{remove ? "保留原照片" : "移除照片"}</button>}
  </div>;
}

export async function savePhoto(path: string, file: File | null, remove: boolean) {
  if (file) return apiFetch<{ url: string }>(path, { method: "POST", body: file, headers: { "Content-Type": file.type } });
  if (remove) return apiFetch<{ url: null }>(path, { method: "DELETE" });
}
