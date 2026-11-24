import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import type { PickupLocation } from "../lib/types";

export const UNLIMITED_MEAL_CAPACITY = 100_000;

export function usePickupLocations(enabled = true) {
  return useQuery({
    queryKey: ["pickup-locations"],
    queryFn: () => apiFetch<PickupLocation[]>("/v1/pickup-locations"),
    enabled,
  });
}

export function allowedPickupLocations<T extends { id: string }>(locations: T[] | undefined, allowedIds: string[] | undefined) {
  if (!locations || !allowedIds?.length) return locations;
  return locations.filter((location) => allowedIds.includes(location.id));
}

export function pickupLocationSummary(allowedIds: string[] | undefined, locations: PickupLocation[] | undefined) {
  if (!allowedIds?.length) return "全部取貨地點";
  const names = new Map(locations?.map((location) => [location.id, location.name]));
  return allowedIds.map((id) => names.get(id) || "已停用的地點").join("、");
}

export function PickupLocationPicker({ value, onChange, legend = "開放的取貨地點", disabled }: {
  value: string[]; onChange: (ids: string[]) => void; legend?: string; disabled?: boolean;
}) {
  const locations = usePickupLocations();
  const all = locations.data ?? [];
  const selected = value.length ? value.filter((id) => all.some((location) => location.id === id)) : all.map((location) => location.id);
  const toggle = (id: string, checked: boolean) => {
    const next = all.map((location) => location.id).filter((locationId) => locationId === id ? checked : selected.includes(locationId));
    if (next.length) onChange(next.length === all.length ? [] : next);
  };
  return <fieldset className="pickup-location-picker" disabled={disabled}>
    <legend>{legend}</legend>
    {locations.isPending ? <p>讀取取貨地點中…</p> : locations.isError ? <p className="form-error">取貨地點暫時無法讀取：{locations.error.message}</p> : <>
      <div className="meal-choice-grid">{all.map((location) => {
        const checked = selected.includes(location.id);
        return <label key={location.id} className="meal-choice">
          <input type="checkbox" checked={checked} disabled={checked && selected.length === 1} onChange={(event) => toggle(location.id, event.target.checked)} />
          {location.name}
        </label>;
      })}</div>
      <p className="field-help">{value.length ? `買家只能選：${pickupLocationSummary(value, all)}` : "全部勾選時，所有取貨地點都開放，之後新增的地點也會開放。"}至少要留一個。</p>
    </>}
  </fieldset>;
}
