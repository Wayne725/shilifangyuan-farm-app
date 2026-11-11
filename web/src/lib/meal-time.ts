import type { MealCalendarEvent, MealEvent, MealEventTiming } from "./types";

type MealBookingType = "standard" | "business";

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

export function mealPeriod(event: MealEventTiming): "lunch" | "dinner" {
  return event.meal_period || (Number(taipeiDateTimeInput(event.pickup_starts_at).slice(11, 13)) >= 15 ? "dinner" : "lunch");
}

export function mealServiceDate(event: MealEventTiming): string {
  return event.service_date || taipeiDate(event.pickup_starts_at);
}

export function formatMealDateTime(value?: string | null): string {
  if (!value) return "時間待公告";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei", month: "numeric", day: "numeric", weekday: "short",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(value));
}

export function formatMealDate(value?: string | null): string {
  return value ? taipeiDate(value).replaceAll("-", "/") : "日期待公告";
}

export function validPickupTime(value: string, event: MealEventTiming, now = Date.now(), bookingType?: MealBookingType): boolean {
  return pickupTimeOptions(event, now, bookingType).some((option) => option.value === value);
}

function usesConfiguredBooking(event: MealEventTiming): boolean {
  return event.booking_policy === "workday" || event.booking_policy === "special";
}

export function mealBookingDeadline(event: MealEventTiming, bookingType: MealBookingType = "standard"): number {
  const deadline = new Date(event.booking_ends_at || event.ordering_ends_at).getTime();
  const priorDayOnly = event.business_only || (bookingType === "business" && event.same_day_preparation_minutes != null);
  if (usesConfiguredBooking(event) && !priorDayOnly) return deadline;
  const cutoff = new Date(`${shiftMealDate(mealServiceDate(event), -1)}T20:00:00+08:00`).getTime();
  return Math.min(cutoff, deadline);
}

export function mealPickupDeadline(event: MealEventTiming, pickupAt: number, bookingType: MealBookingType = "standard"): number {
  const deadline = mealBookingDeadline(event, bookingType);
  const preparationMinutes = event.same_day_preparation_minutes;
  return bookingType === "standard" && preparationMinutes != null
    ? Math.min(deadline, pickupAt - preparationMinutes * 60_000)
    : deadline;
}

export function mealOrderingOpen(event: MealEventTiming, now = Date.now(), bookingType: MealBookingType = event.business_only ? "business" : "standard"): boolean {
  return (!event.business_only || bookingType === "business")
    && ["published", "pickup_open"].includes(event.status)
    && !event.new_orders_blocked_reason
    && (usesConfiguredBooking(event) || mealServiceDate(event) === shiftMealDate(taipeiDate(now), 1))
    && new Date(event.booking_starts_at || event.ordering_starts_at).getTime() <= now
    && mealBookingDeadline(event, bookingType) > now;
}

export function mealMenuAvailability(offerings: MealEvent["offerings"]) {
  return {
    has_active_offerings: offerings.some((offering) => offering.is_active),
    has_available_offerings: offerings.some((offering) => offering.is_active && offering.available_quantity > 0),
  };
}

export function mealBookingStatus(event: MealEvent | MealCalendarEvent, now = Date.now(), bookingType: MealBookingType = event.business_only ? "business" : "standard"): string {
  if (event.new_orders_blocked_reason) return "暫停預訂";
  const today = taipeiDate(now);
  const serviceDate = mealServiceDate(event);
  const acceptsSameDay = bookingType === "standard" && event.same_day_preparation_minutes != null;
  if (serviceDate < today || (serviceDate === today && !acceptsSameDay) || event.status === "ordering_closed"
    || mealBookingDeadline(event, bookingType) <= now
    || new Date(event.pickup_ends_at).getTime() <= now) return "已截止";
  if (!mealOrderingOpen(event, now, bookingType)) return "尚未開放";
  const menu = "offerings" in event ? mealMenuAvailability(event.offerings) : event;
  if (!menu.has_active_offerings) return "菜單待公告";
  if (!menu.has_available_offerings) return "已售完";
  if (!pickupTimeOptions(event, now, bookingType).length) return "已截止";
  return "可預訂";
}

export function pickupTimeOptions(event: MealEventTiming, now = Date.now(), bookingType: MealBookingType = event.business_only ? "business" : "standard"): Array<{ value: string; label: string }> {
  if (event.new_orders_blocked_reason || (event.business_only && bookingType !== "business")) return [];
  if (!usesConfiguredBooking(event) && mealServiceDate(event) !== shiftMealDate(taipeiDate(now), 1)) return [];
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
      if (!allowed.has(value) || timestamp < start || timestamp >= new Date(event.pickup_ends_at).getTime() || !withinEnd || timestamp <= now) return false;
      return bookingType !== "standard" || event.same_day_preparation_minutes == null
        || now < mealPickupDeadline(event, timestamp, bookingType);
    })
    .sort()
    .map((value) => ({ value, label: value.slice(11) }));
}

export function mealPickupChoices(events: MealCalendarEvent[], date: string, now = Date.now()) {
  const today = taipeiDate(now);
  return events.filter((event) => mealServiceDate(event) === date && !["draft", "cancelled", "completed"].includes(event.status))
    .flatMap((event) => {
      const status = mealBookingStatus(event, now);
      if (date === today && status !== "可預訂") return [];
      return pickupTimeOptions(event, now).map((option) => ({
        ...option, eventId: event.id, location: event.location, title: event.title, status, businessOnly: event.business_only === true,
      }));
    }).sort((left, right) => left.value.localeCompare(right.value) || left.location.localeCompare(right.location) || left.eventId.localeCompare(right.eventId));
}

export function mealBookingDates(events: MealCalendarEvent[], now = Date.now()) {
  const today = taipeiDate(now);
  const upcoming = events.filter((event) => {
    if (event.new_orders_blocked_reason || ["draft", "cancelled", "completed"].includes(event.status)) return false;
    const date = mealServiceDate(event);
    return date > today || (date === today && mealBookingStatus(event, now) === "可預訂");
  });
  return [...new Set(upcoming.map(mealServiceDate))].sort().map((date) => {
    const statuses = upcoming.filter((event) => mealServiceDate(event) === date)
      .map((event) => mealBookingStatus(event, now));
    const status = ["可預訂", "尚未開放", "菜單待公告", "已售完", "已截止"]
      .find((value) => statuses.includes(value)) || "菜單待公告";
    return { date, status };
  });
}
