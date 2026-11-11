import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { mealBookingDates, mealPickupChoices } from "../lib/meal-time";
import type { MealCalendarEvent } from "../lib/types";

type Props = {
  events: MealCalendarEvent[];
  now: number;
};

const weekdays = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"];

export function MealDateNavigation({ events, now }: Props) {
  const [selectedDate, setSelectedDate] = useState("");
  const dates = mealBookingDates(events, now);
  const preferred = dates.find((entry) => entry.status === "可預訂") || dates.find((entry) => entry.status !== "已截止") || dates[0];
  const selected = dates.find((entry) => entry.date === selectedDate) || preferred;
  const choices = selected ? mealPickupChoices(events, selected.date, now) : [];
  const locations = [...new Set(choices.map((choice) => choice.location))];

  return <section className="meal-date-navigation" aria-label="便當日期與取餐時間">
    {dates.length > 0 && <div className="meal-date-strip" role="group" aria-label="取餐日期">
      {dates.map(({ date, status }) => {
        const day = new Date(`${date}T12:00:00+08:00`).getUTCDay();
        const monthDay = `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`;
        return <button key={date} className="meal-date-button" type="button"
          aria-pressed={selected?.date === date} onClick={() => setSelectedDate(date)}
          aria-label={`${monthDay} ${weekdays[day]}，${status}`}>
          <strong>{monthDay}</strong><span className="meal-date-weekday">{weekdays[day]}</span><span className="meal-date-status">{status}</span>
        </button>;
      })}
    </div>}
    <div className="meal-time-selection"><h3>選擇取餐時間</h3>
      {locations.map((location) => <section className="meal-time-location" key={location} aria-label={`${location}取餐時間`}>
        <h4>{location}</h4>
        <div className="meal-pickup-options">{choices.filter((choice) => choice.location === location).map((choice) => {
          const duplicate = choices.filter((other) => other.location === location && other.value === choice.value).length > 1;
          const name = `${choice.label}${duplicate ? ` · ${choice.title}` : ""}${choice.businessOnly ? " · 會餐" : ""}`;
          return choice.status === "可預訂"
            ? <Link key={`${choice.eventId}-${choice.value}`} to="/meals/$eventId" params={{ eventId: choice.eventId }} search={{ pickup_at: choice.value }} aria-label={`${location} ${name} 取餐`}>{name}</Link>
            : <button key={`${choice.eventId}-${choice.value}`} type="button" disabled aria-label={`${location} ${name}，${choice.status}`}>{name}<small>{choice.status}</small></button>;
        })}</div>
      </section>)}
      {!choices.length && <p className="meal-selection-notice">{selected
        ? selected.status === "已截止" ? "這一天的取餐時段已截止" : "這一天的取餐時段尚未開放"
        : "目前沒有已公告的供餐日期"}</p>}
    </div>
  </section>;
}
