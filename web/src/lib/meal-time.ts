import type { MealEvent } from "./types";

const taipeiDateParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function taipeiDateTimeInput(value: string | number | Date): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = Object.fromEntries(taipeiDateParts.formatToParts(date).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function taipeiDate(value: string | number | Date = Date.now()): string {
  return taipeiDateTimeInput(value).slice(0, 10);
}

export function taipeiInputToIso(value: string): string {
  return new Date(`${value}:00+08:00`).toISOString();
}

export function shiftMealDate(value: string, days: number): string {
  return taipeiDate(new Date(`${value}T12:00:00+08:00`).getTime() + days * 86400000);
}

export function mealPeriod(event: MealEvent): "lunch" | "dinner" {
  return event.meal_period || (Number(taipeiDateTimeInput(event.pickup_starts_at).slice(11, 13)) >= 15 ? "dinner" : "lunch");
}

export function mealServiceDate(event: MealEvent): string {
  return event.service_date || taipeiDate(event.pickup_starts_at);
}

export function formatMealDateTime(value?: string | null): string {
  if (!value) return "時間待公告";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei", month: "numeric", day: "numeric", weekday: "short",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(value));
}

export function validPickupTime(value: string, event: MealEvent, now = Date.now()): boolean {
  return pickupTimeOptions(event, now).some((option) => option.value === value);
}

export function mealOrderingOpen(event: MealEvent, now = Date.now()): boolean {
  return ["published", "pickup_open"].includes(event.status)
    && mealServiceDate(event) === shiftMealDate(taipeiDate(now), 1)
    && new Date(event.booking_starts_at || event.ordering_starts_at).getTime() <= now
    && new Date(event.booking_ends_at || event.ordering_ends_at).getTime() > now;
}

export function mealBookingStatus(event: MealEvent, now = Date.now()): string {
  if (mealServiceDate(event) <= taipeiDate(now) || event.status === "ordering_closed"
    || new Date(event.booking_ends_at || event.ordering_ends_at).getTime() <= now
    || new Date(event.pickup_ends_at).getTime() <= now) return "已截止";
  if (!mealOrderingOpen(event, now)) return "尚未開放";
  const menu = event.offerings.filter((offering) => offering.is_active);
  if (!menu.length) return "菜單待公告";
  if (!menu.some((offering) => offering.available_quantity > 0)) return "已售完";
  if (!pickupTimeOptions(event, now).length) return "已截止";
  return "可預訂";
}

export function pickupTimeOptions(event: MealEvent, now = Date.now()): Array<{ value: string; label: string }> {
  if (mealServiceDate(event) !== shiftMealDate(taipeiDate(now), 1)) return [];
  const start = new Date(event.pickup_starts_at).getTime();
  const endInclusive = event.pickup_slot_ends_at != null;
  const end = new Date(event.pickup_slot_ends_at ?? event.pickup_ends_at).getTime();
  const serviceDate = mealServiceDate(event);
  const clocks = mealPeriod(event) === "lunch"
    ? ["11:00", "11:30", "12:00", "12:30", "13:00"]
    : ["16:30", "17:00", "17:30", "18:00"];
  const allowed = new Set(clocks.map((clock) => `${serviceDate}T${clock}`));
  const slots = event.pickup_slots ?? clocks.map((clock) => `${serviceDate}T${clock}:00+08:00`);
  return [...new Set(slots.map((slot) => taipeiDateTimeInput(slot)))]
    .filter((value) => {
      const timestamp = new Date(`${value}:00+08:00`).getTime();
      const withinEnd = endInclusive ? timestamp <= end : timestamp < end;
      return allowed.has(value) && timestamp >= start && timestamp < new Date(event.pickup_ends_at).getTime() && withinEnd && timestamp > now;
    })
    .sort()
    .map((value) => ({ value, label: value.slice(11) }));
}

export function mealPickupChoices(events: MealEvent[], date: string, now = Date.now()) {
  return events.filter((event) => mealServiceDate(event) === date && !["draft", "cancelled", "completed"].includes(event.status))
    .flatMap((event) => pickupTimeOptions(event, now).map((option) => ({
      ...option, eventId: event.id, location: event.location, title: event.title,
      status: mealBookingStatus(event, now),
    }))).sort((left, right) => left.value.localeCompare(right.value) || left.location.localeCompare(right.location) || left.eventId.localeCompare(right.eventId));
}
