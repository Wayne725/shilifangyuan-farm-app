import { useEffect, useState } from "react";
import { campaignTimestamp } from "./group-campaigns";

export function useDeadlineClock(deadline?: string | null) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const update = () => setNow(Date.now());
    update();
    const remaining = campaignTimestamp(deadline) - Date.now();
    const boundary = remaining > 0 ? window.setTimeout(update, Math.min(remaining + 5, 2147483647)) : undefined;
    const timer = window.setInterval(update, 30000);
    return () => { window.clearTimeout(boundary); window.clearInterval(timer); };
  }, [deadline]);
  return now;
}
