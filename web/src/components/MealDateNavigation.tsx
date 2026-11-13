import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { mealBookingDates, mealPeriod, mealPickupChoices, mealServiceDate } from "../lib/meal-time";
import type { MealCalendarEvent, MealDisasterAnnouncement } from "../lib/types";

type Props = {
  events: MealCalendarEvent[];
  announcements: MealDisasterAnnouncement[];
  now: number;
};

const weekdays = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"];
const periodLabels = { lunch: "午餐", dinner: "晚餐" };
const stoppedStatus = "颱風暫停供餐";
const periodsInOrder = ["lunch", "dinner"] as const;

function mealPeriodNames(periods: MealDisasterAnnouncement["periods"], served: MealDisasterAnnouncement["periods"]) {
  return periodsInOrder.filter((period) => periods.includes(period))
    .map((period) => `${served.includes(period) ? "部分" : ""}${periodLabels[period]}`).join("、");
}

export function MealDateNavigation({ events, announcements, now }: Props) {
  const [selectedDate, setSelectedDate] = useState("");
  const noticesByDate = new Map<string, MealDisasterAnnouncement[]>();
  for (const notice of announcements) {
    const existing = noticesByDate.get(notice.service_date) ?? [];
    const periods = notice.periods.filter((period) => !existing.some((entry) => entry.periods.includes(period)));
    if (periods.length) noticesByDate.set(notice.service_date, [...existing, { ...notice, periods }]);
  }
  const cancelledIds = new Set([...noticesByDate.values()].flatMap((notices) =>
    notices.flatMap((notice) => notice.cancelled_event_ids ?? [])));
  const serviceEvents = events.filter((event) => !cancelledIds.has(event.id));
  const bookingStatuses = new Map(mealBookingDates(serviceEvents, now).map(({ date, status }) => [date, status]));
  const dates = [...new Set([...bookingStatuses.keys(), ...noticesByDate.keys()])].sort().map((date) => ({
    date,
    status: bookingStatuses.get(date) ?? stoppedStatus,
    notices: noticesByDate.get(date) ?? [],
    served: noticesByDate.has(date)
      ? serviceEvents.filter((event) => mealServiceDate(event) === date).map(mealPeriod) : [],
  }));
  const preferred = dates.find((entry) => entry.status === "可預訂") || dates.find((entry) => entry.status !== "已截止") || dates[0];
  const selected = dates.find((entry) => entry.date === selectedDate) || preferred;
  const choices = selected ? mealPickupChoices(serviceEvents, selected.date, now) : [];
  const locations = [...new Set(choices.map((choice) => choice.location))];

  return <section className="meal-date-navigation" aria-label="便當日期與取餐時間">
    {dates.length > 0 && <div className="meal-date-strip" role="group" aria-label="取餐日期">
      {dates.map(({ date, status, notices, served }) => {
        const day = new Date(`${date}T12:00:00+08:00`).getUTCDay();
        const monthDay = `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`;
        const stoppedLabel = status !== stoppedStatus && notices.length
          ? `${mealPeriodNames(notices.flatMap((notice) => notice.periods), served)}停供` : "";
        return <button key={date} className="meal-date-button" type="button"
          data-service-notice={notices.length > 0 ? "" : undefined}
          aria-pressed={selected?.date === date} onClick={() => setSelectedDate(date)}
          aria-label={`${monthDay} ${weekdays[day]}，${status}${stoppedLabel ? `，${stoppedLabel}` : ""}`}>
          <strong>{monthDay}</strong><span className="meal-date-weekday">{weekdays[day]}</span>
          <span className="meal-date-status">{status}{stoppedLabel && <span className="meal-date-disruption">{stoppedLabel}</span>}</span>
        </button>;
      })}
    </div>}
    <div className="meal-time-selection"><h3>選擇取餐時間</h3>
      {!!selected?.notices.length && <ul className="meal-pickup-notices" role="list" aria-label="停餐資訊">
        {selected.notices.map((notice) => <li key={notice.periods.join("-")}>
          {mealPeriodNames(notice.periods, selected.served)} · {notice.message || stoppedStatus}
        </li>)}
      </ul>}
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
      {!choices.length && !selected?.notices.length && <p className="meal-selection-notice">{selected
        ? selected.status === "已截止" ? "這一天的取餐時段已截止" : "這一天的取餐時段尚未開放"
        : "目前沒有已公告的供餐日期"}</p>}
    </div>
  </section>;
}
