import type { MealEvent } from "../lib/types";

export function MealEventConflicts({ events, location, startsAt, endsAt, editingId }: {
  events: MealEvent[];
  location: string;
  startsAt: string;
  endsAt: string;
  editingId?: string;
}) {
  const start = Date.parse(`${startsAt}+08:00`);
  const end = Date.parse(`${endsAt}+08:00`);
  const matches = location.trim() ? events.filter((event) => event.id !== editingId
    && !["cancelled", "completed"].includes(event.status)
    && event.location.trim() === location.trim()
    && Date.parse(event.pickup_starts_at) < end && Date.parse(event.pickup_ends_at) > start) : [];
  if (!matches.length) return null;
  return <aside className="meal-event-conflicts" role="status">
    <strong>相同地點已有重疊場次</strong>
    <ul>{matches.map((event) => <li key={event.id}>{event.title}{event.schedule_template_id ? "（循環排程）" : "（手動場次）"}</li>)}</ul>
  </aside>;
}
