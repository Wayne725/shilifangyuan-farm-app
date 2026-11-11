export function mealCalendarFixture(events) {
  return events.map(({ offerings, ...event }) => ({
    ...event,
    has_active_offerings: offerings.some(row => row.is_active),
    has_available_offerings: offerings.some(row => row.is_active && row.available_quantity > 0),
  }));
}
