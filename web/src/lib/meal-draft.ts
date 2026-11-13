import { pickupTimeOptions } from "./meal-time";
import { sessionStore } from "./browser-storage";
import type { MealEvent, PickupLocation } from "./types";

export interface MealDraft {
  mealBookingType?: "business";
  businessCash?: true;
  quantities: Record<string, number>;
  selections: Record<string, Record<string, string[]>>;
  portionSelections?: Record<string, Record<string, string[]>[]>;
  pickupAt: string;
  pickupLocationId: string;
  needsUtensils: boolean;
}

type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const storageKey = (eventId: string) => `slf_meal_draft_v1:${eventId}`;

export function emptyMealDraft(): MealDraft {
  return { quantities: {}, selections: {}, pickupAt: "", pickupLocationId: "", needsUtensils: false };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readSelections(value: unknown): Record<string, string[]> {
  return Object.fromEntries(Object.entries(isRecord(value) ? value : {})
    .filter(([, options]) => Array.isArray(options))
    .map(([group, options]) => [group, [...new Set((options as unknown[]).filter((option): option is string => typeof option === "string"))]]));
}

export function readMealDraft(eventId: string, storage?: DraftStorage): MealDraft {
  try {
    const value: unknown = JSON.parse((storage ?? sessionStore).getItem(storageKey(eventId)) || "null");
    if (!isRecord(value)) return emptyMealDraft();
    const quantities = Object.fromEntries(
      Object.entries(isRecord(value.quantities) ? value.quantities : {})
        .filter(([, quantity]) => typeof quantity === "number" && Number.isInteger(quantity) && quantity > 0)
        .map(([id, quantity]) => [id, Math.min(quantity as number, 99)]),
    );
    const selections = Object.fromEntries(
      Object.entries(isRecord(value.selections) ? value.selections : {})
        .filter(([, groups]) => isRecord(groups))
        .map(([id, groups]) => [id, readSelections(groups)]),
    );
    const portionSelections = Object.fromEntries(Object.entries(isRecord(value.portionSelections) ? value.portionSelections : {})
      .filter(([, portions]) => Array.isArray(portions))
      .map(([id, portions]) => [id, (portions as unknown[]).slice(0, 99).map(readSelections)]));
    return {
      ...(value.mealBookingType === "business" ? { mealBookingType: "business" } : {}),
      ...(value.businessCash === true ? { businessCash: true } : {}),
      quantities,
      selections,
      ...(Object.keys(portionSelections).length ? { portionSelections } : {}),
      pickupAt: typeof value.pickupAt === "string" ? value.pickupAt : "",
      pickupLocationId: typeof value.pickupLocationId === "string" ? value.pickupLocationId : "",
      needsUtensils: value.needsUtensils === true,
    };
  } catch {
    return emptyMealDraft();
  }
}

export function writeMealDraft(eventId: string, draft: MealDraft, storage?: DraftStorage): void {
  try {
    const target = storage ?? sessionStore;
    if (draft.mealBookingType !== "business" && !draft.businessCash && !draft.pickupAt && !draft.pickupLocationId && !draft.needsUtensils && !Object.values(draft.quantities).some((quantity) => quantity > 0)) {
      target.removeItem(storageKey(eventId));
      return;
    }
    target.setItem(storageKey(eventId), JSON.stringify({
      ...(draft.mealBookingType === "business" ? { mealBookingType: "business" } : {}),
      ...(draft.businessCash === true ? { businessCash: true } : {}),
      quantities: draft.quantities, selections: draft.selections, portionSelections: draft.portionSelections, pickupAt: draft.pickupAt,
      pickupLocationId: draft.pickupLocationId, needsUtensils: draft.needsUtensils,
    }));
  } catch {
    // Browser storage may be disabled; selecting meals must still work.
  }
}

export function clearMealDraft(eventId: string, storage?: DraftStorage): void {
  try {
    (storage ?? sessionStore).removeItem(storageKey(eventId));
  } catch {
    // Clearing the in-memory selection remains available without browser storage.
  }
}

export function reconcileMealDraft(draft: MealDraft, event: MealEvent, now = Date.now(), locations?: PickupLocation[]): MealDraft {
  const next = emptyMealDraft();
  if (draft.mealBookingType === "business") next.mealBookingType = "business";
  if (draft.businessCash && (event.business_only || draft.mealBookingType === "business")) next.businessCash = true;
  next.needsUtensils = draft.needsUtensils;
  next.pickupLocationId = locations === undefined || locations.some((location) => location.is_active && location.id === draft.pickupLocationId)
    ? draft.pickupLocationId
    : locations.find((location) => location.is_active)?.id || "";
  for (const offering of event.offerings) {
    if (!offering.is_active) continue;
    const quantity = Math.min(draft.quantities[offering.id] || 0, offering.available_quantity, 99);
    if (quantity <= 0) continue;
    next.quantities[offering.id] = quantity;
    const clean = (input: Record<string, string[]> = {}) => {
      const groups: Record<string, string[]> = {};
      for (const group of offering.option_groups) {
        if (!group.is_active) continue;
        const allowed = new Set(group.options.filter((option) => option.is_active).map((option) => option.id));
        const options = [...new Set(input[group.id] || [])].filter((id) => allowed.has(id)).slice(0, group.max_selections);
        if (options.length) groups[group.id] = options;
      }
      return groups;
    };
    const groups = clean(draft.selections[offering.id]);
    if (Object.keys(groups).length) next.selections[offering.id] = groups;
    const portions = draft.portionSelections?.[offering.id];
    if (portions && quantity > 1 && (event.business_only || draft.mealBookingType === "business")) {
      (next.portionSelections ??= {})[offering.id] = Array.from({ length: quantity }, (_, index) => clean(portions[index] ?? groups));
    }
  }
  if (event.ordering_paused || pickupTimeOptions(event, now, draft.mealBookingType).some((option) => option.value === draft.pickupAt)) {
    next.pickupAt = draft.pickupAt;
  }
  return next;
}

export function mealDraftWasAdjusted(original: MealDraft, current: MealDraft): boolean {
  return Boolean(original.pickupAt && original.pickupAt !== current.pickupAt)
    || Boolean(original.pickupLocationId && original.pickupLocationId !== current.pickupLocationId)
    || Object.entries(original.quantities).some(([id, quantity]) => quantity > 0 && quantity !== current.quantities[id])
    || Object.entries(original.selections).some(([id, groups]) => current.quantities[id] > 0
      && Object.entries(groups).some(([groupId, options]) => options.some((option) => !current.selections[id]?.[groupId]?.includes(option))));
}
