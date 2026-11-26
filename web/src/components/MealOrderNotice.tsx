import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";

import { formatMealDateTime, mealPeriod, mealServiceDate, taipeiDateTimeInput } from "../lib/meal-time";
import type { MealEvent } from "../lib/types";
import "./MealOrderNotice.css";

const weekdayNames = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"];

function seenKey(eventId: string) {
  return `meal-order-notice-seen:${eventId}`;
}

function alreadySeen(eventId: string) {
  try {
    return window.sessionStorage.getItem(seenKey(eventId)) === "1";
  } catch {
    return false;
  }
}

function rememberSeen(eventId: string) {
  try {
    window.sessionStorage.setItem(seenKey(eventId), "1");
  } catch {
    // Private browsing can block storage; the notice then shows again next visit.
  }
}

function serviceDateLabel(date: string, relative: string) {
  const [year, month, day] = date.split("-").map(Number);
  const weekday = weekdayNames[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
  return `${month}/${day}（${weekday}${relative ? `，${relative}` : ""}）`;
}

export function MealOrderNotice({ event, notice, relativeDay, deadline, showDeadline }: {
  event: MealEvent;
  notice?: { title: string; message: string } | null;
  relativeDay: string;
  deadline: string;
  showDeadline: boolean;
}) {
  const [dismissed, setDismissed] = useState(() => alreadySeen(event.id));
  if (!notice) return null;
  const period = mealPeriod(event) === "dinner" ? "晚餐" : "午餐";
  const close = () => {
    rememberSeen(event.id);
    setDismissed(true);
  };
  return <Dialog.Root open={!dismissed} onOpenChange={open => { if (!open) close(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="meal-notice-overlay" />
      <Dialog.Content className="meal-notice-dialog">
        <Dialog.Title>{notice.title}</Dialog.Title>
        <Dialog.Description asChild>
          <div className="meal-notice-body">
            <p className="meal-notice-date">歡迎預訂 <strong>{serviceDateLabel(mealServiceDate(event), relativeDay)}的{period}</strong></p>
            <p>取餐時間：{taipeiDateTimeInput(event.pickup_starts_at).slice(11)}–{taipeiDateTimeInput(event.pickup_ends_at).slice(11)}</p>
            {event.business_only && <p>這一場只開放會餐。</p>}
            {showDeadline && <p>請於 {formatMealDateTime(deadline)} 前完成預訂。</p>}
            {notice.message && <p className="meal-notice-message">{notice.message}</p>}
          </div>
        </Dialog.Description>
        <div className="form-actions">
          <Dialog.Close asChild><button className="button button-primary" type="button">我知道了</button></Dialog.Close>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
