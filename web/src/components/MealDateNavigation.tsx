import { Link } from "@tanstack/react-router";

import { mealBookingStatus, mealPickupChoices, mealServiceDate, shiftMealDate, taipeiDate } from "../lib/meal-time";
import type { MealEvent } from "../lib/types";

type Props = {
  events: MealEvent[];
  now: number;
};

const weekdays = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"];

export function MealDateNavigation({ events, now }: Props) {
  const tomorrow = shiftMealDate(taipeiDate(now), 1);
  const dateEvents = events.filter((event) => mealServiceDate(event) === tomorrow);
  const statuses = dateEvents.map((event) => mealBookingStatus(event, now));
  const status = ["可預訂", "尚未開放", "菜單待公告", "已售完", "已截止"].find((value) => statuses.includes(value)) || "菜單待公告";
  const day = new Date(`${tomorrow}T12:00:00+08:00`).getUTCDay();
  const monthDay = `${Number(tomorrow.slice(5, 7))}/${Number(tomorrow.slice(8, 10))}`;
  const choices = mealPickupChoices(events, tomorrow, now);
  const locations = [...new Set(choices.map((choice) => choice.location))];

  return <section className="meal-date-navigation" aria-label="便當日期與取餐時間">
    <div className="meal-date-summary" aria-label="取餐日期">
      <strong>明天 {monthDay}（{weekdays[day]}）</strong>
      <span className="meal-date-status">{status}</span>
    </div>
    <div className="meal-time-selection"><h3>選擇取餐時間</h3>
      {locations.map((location) => <section className="meal-time-location" key={location} aria-label={`${location}取餐時間`}>
        <h4>{location}</h4>
        <div className="meal-pickup-options">{choices.filter((choice) => choice.location === location).map((choice) => {
          const duplicate = choices.filter((other) => other.location === location && other.value === choice.value).length > 1;
          const name = `${choice.label}${duplicate ? ` · ${choice.title}` : ""}`;
          return choice.status === "可預訂"
            ? <Link key={`${choice.eventId}-${choice.value}`} to="/meals/$eventId" params={{ eventId: choice.eventId }} search={{ pickup_at: choice.value }} aria-label={`${location} ${name} 取餐`}>{name}</Link>
            : <button key={`${choice.eventId}-${choice.value}`} type="button" disabled aria-label={`${location} ${name}，${choice.status}`}>{name}<small>{choice.status}</small></button>;
        })}</div>
      </section>)}
      {!choices.length && <p className="meal-selection-notice">明天的取餐時段尚未開放</p>}
    </div>
  </section>;
}
