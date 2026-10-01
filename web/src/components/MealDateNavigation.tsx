import { mealBookingStatus, mealPeriod, mealServiceDate, shiftMealDate, taipeiDate } from "../lib/meal-time";
import type { MealEvent } from "../lib/types";

type Props = {
  events: MealEvent[];
  selectedDate: string;
  onDateChange: (date: string) => void;
  period: string;
  onPeriodChange: (period: string) => void;
  now: number;
};

const weekdays = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"];

function summary(events: MealEvent[], now: number): string {
  const statuses = events.map((event) => mealBookingStatus(event, now));
  return ["可預訂", "尚未開放", "菜單待公告", "已售完", "已截止"].find((status) => statuses.includes(status)) || "尚未開放";
}

export function MealDateNavigation({ events, selectedDate, onDateChange, period, onPeriodChange, now }: Props) {
  const today = taipeiDate(now);
  const tomorrow = shiftMealDate(today, 1);
  const dates = [...new Set([
    ...Array.from({ length: 8 }, (_, index) => shiftMealDate(today, index)),
    ...events.map(mealServiceDate),
  ])].sort();
  const selectedEvents = events.filter((event) => mealServiceDate(event) === selectedDate);

  return <section className="meal-date-navigation" aria-label="便當日期與餐別">
    <div className="meal-date-heading">
      <h3>選擇取餐日期</h3>
      <p>台灣時間 · 日期可左右滑動</p>
    </div>
    <div className="meal-date-strip" role="group" aria-label="取餐日期">
      {dates.map((date) => {
        const day = new Date(`${date}T12:00:00+08:00`).getUTCDay();
        const relative = date === today ? "今天" : date === tomorrow ? "明天" : "";
        const monthDay = `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`;
        const dateEvents = events.filter((event) => mealServiceDate(event) === date);
        const status = !dateEvents.length && day === 0 ? "休息" : summary(dateEvents, now);
        return <button key={date} type="button" className="meal-date-button"
          aria-label={`${relative ? `${relative} ` : ""}${monthDay}（${weekdays[day]}），${status}`}
          aria-pressed={selectedDate === date} disabled={!dateEvents.length}
          onClick={() => onDateChange(date)}>
          <span className="meal-date-weekday">{relative ? `${relative} · ` : ""}{weekdays[day]}</span>
          <strong>{monthDay}</strong>
          <span className="meal-date-status">{status}</span>
        </button>;
      })}
    </div>
    <div className="meal-period-filter">
      <h3>選擇餐別</h3>
      <div className="meal-period-options" role="group" aria-label="餐別">
        <button type="button" aria-pressed={period === "all"} onClick={() => onPeriodChange("all")}>全部</button>
        {(["lunch", "dinner"] as const).map((value) => {
          const periodEvents = selectedEvents.filter((event) => mealPeriod(event) === value);
          const label = value === "lunch" ? "午餐" : "晚餐";
          const status = periodEvents.length ? summary(periodEvents, now) : "無場次";
          return <button key={value} type="button" aria-label={`${label}，${status}`}
            aria-pressed={period === value} disabled={!periodEvents.length}
            onClick={() => onPeriodChange(value)}>
            <span>{label}</span><small>{status}</small>
          </button>;
        })}
      </div>
    </div>
  </section>;
}
