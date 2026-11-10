import { formatDateTime } from "../lib/api";
import type { MealCancellationRequest } from "../lib/types";

export function CancellationReviewDetails({ request }: { request: MealCancellationRequest }) {
  if (request.status === "pending") return null;
  return <>
    <p>處理人：{request.reviewed_by_name || "合作社管理員"}</p>
    <p>處理時間：{request.reviewed_at ? formatDateTime(request.reviewed_at) : "未記錄"}</p>
  </>;
}
