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
  if (!value) return false;
  const timestamp = new Date(`${value}:00+08:00`).getTime();
  return timestamp >= new Date(event.pickup_starts_at).getTime()
    && timestamp < new Date(event.pickup_ends_at).getTime()
    && timestamp > now;
}

export function mealOrderingOpen(event: MealEvent, now = Date.now()): boolean {
  return ["published", "pickup_open"].includes(event.status)
    && new Date(event.ordering_starts_at).getTime() <= now
    && new Date(event.ordering_ends_at).getTime() > now;
}

export function mealBookingStatus(event: MealEvent, now = Date.now()): string {
  if (event.status === "ordering_closed" || new Date(event.ordering_ends_at).getTime() <= now
    || new Date(event.pickup_ends_at).getTime() <= now) return "已截止";
  if (!mealOrderingOpen(event, now)) return "尚未開放";
  const menu = event.offerings.filter((offering) => offering.is_active);
  if (!menu.length) return "菜單待公告";
  if (!menu.some((offering) => offering.available_quantity > 0)) return "已售完";
  if (!pickupTimeOptions(event, now).length) return "已截止";
  return "可預訂";
}

export function pickupTimeOptions(event: MealEvent, now = Date.now()): Array<{ value: string; label: string }> {
  const options = [];
  const end = new Date(event.pickup_ends_at).getTime();
  const first = Math.ceil(new Date(event.pickup_starts_at).getTime() / 60000) * 60000;
  const serviceDate = mealServiceDate(event);
  for (let timestamp = first; timestamp < end; timestamp += 10 * 60000) {
    if (timestamp <= now) continue;
    options.push({
      value: taipeiDateTimeInput(timestamp),
      label: `${taipeiDate(timestamp) !== serviceDate ? "次日 " : ""}${formatMealDateTime(new Date(timestamp).toISOString())}`,
    });
  }
  return options;
}
