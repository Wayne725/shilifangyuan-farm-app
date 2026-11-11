import { ApiError } from "./api";
import { taipeiDateTimeInput } from "./meal-time";
import type { GroupCampaign } from "./types";

export function campaignTimestamp(value?: string | null) {
  if (!value) return NaN;
  return new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`).getTime();
}

export function campaignDateTime(value?: string | null) {
  const timestamp = campaignTimestamp(value);
  return Number.isFinite(timestamp) ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(timestamp) : "時間待公告";
}

export function campaignDateInput(value?: string | null) {
  const timestamp = campaignTimestamp(value);
  return Number.isFinite(timestamp) ? taipeiDateTimeInput(timestamp) : "";
}

export function defaultReviewDeadline(deadline: string) {
  return taipeiDateTimeInput(new Date(`${deadline}:00+08:00`).getTime() + 72 * 3600000);
}

export function campaignOrderBlockReason(campaign: GroupCampaign, now = Date.now()) {
  if (campaign.is_public === false) return "此共同購買已停止公開";
  if (!["recruiting", "confirmed"].includes(campaign.decision_status)) return campaign.decision_status === "pending_confirmation" ? "已截止，等待管理員審核" : "此共同購買已停止接單";
  if (!Number.isFinite(campaignTimestamp(campaign.deadline))) return "付款截止時間待確認";
  if (campaignTimestamp(campaign.deadline) <= now) return campaign.intake_status === "settling" ? "已截止，付款結算中" : "共同購買已截止";
  if (campaign.intake_status !== "open" || campaign.available_quantity <= 0) return campaign.intake_status === "full" ? "本團供應已額滿" : "此共同購買目前未開放接單";
  return "";
}

export function campaignStatusLabel(campaign: GroupCampaign, now = Date.now()) {
  const final = { confirmed: "已成團", rejected: "不成團", failed_unmet: "未達門檻", expired_unconfirmed: "逾期未確認", cancelled: "已取消" }[campaign.decision_status];
  if (final) return final;
  if (campaign.intake_status === "settling") return "付款結算中";
  if (campaign.decision_status === "pending_confirmation" || campaignTimestamp(campaign.deadline) <= now) return "待成團審核";
  if (campaign.intake_status === "full") return "供應額滿";
  return campaign.paid_quantity >= campaign.min_paid_quantity ? "已達門檻，持續集單" : "集單中";
}

export function campaignConfirmBlockReason(campaign: GroupCampaign, now = Date.now()) {
  if (!Number.isFinite(campaignTimestamp(campaign.deadline))) return "付款截止時間待確認";
  if (campaignTimestamp(campaign.deadline) > now) return "付款截止後開放審核";
  if (campaign.intake_status === "settling") return "付款尚在結算，暫時無法確認成團";
  if (campaign.paid_quantity <= 0) return "沒有有效付款，不能確認空團";
  if (!campaign.can_confirm || campaign.decision_status !== "pending_confirmation" || campaign.intake_status !== "closed" || !Number.isInteger(campaign.threshold_version)) return campaign.confirmation_blocked_reason || "目前無法確認成團，請更新共同購買資料";
  return "";
}

export function campaignScheduleError(deadline: string, reviewDeadline: string, pickupStart: string, pickupEnd: string, now = Date.now(), editing = false) {
  const values = [deadline, reviewDeadline, pickupStart, pickupEnd].map(value => value ? new Date(`${value}:00+08:00`).getTime() : NaN);
  if (!values.every(Number.isFinite)) return "請填寫完整的付款截止、處理提醒日與取貨時間";
  const [payment, review, start, end] = values;
  if (!editing && payment <= now) return "付款截止必須晚於現在";
  if (review <= payment || review <= now) return "處理提醒日須晚於付款截止及現在";
  if (start < review) return "預計取貨開始不可早於處理提醒日";
  if (end <= start) return "預計取貨結束須晚於開始時間";
  return "";
}

export function campaignAccessDenied(error: unknown) {
  return error instanceof ApiError && [401, 403].includes(error.status);
}

export function retryCampaignRead(failureCount: number, error: Error) {
  return !campaignAccessDenied(error) && failureCount < 1;
}

export function groupProposalStatusLabel(status: string) {
  return { pending_review: "待審核", voting: "投票中", ended_unmet: "未達投票門檻", conversion_pending: "待正式開團", converted: "已建立正式共同購買", rejected: "未通過", expired_unhandled: "逾期未開團", expired: "已結束" }[status] || status;
}

export function campaignQuantityBlockReason(campaign: GroupCampaign, now = Date.now()) {
  if (!["recruiting", "pending_confirmation", "confirmed"].includes(campaign.decision_status)) return "共同購買已結束";
  if (campaignTimestamp(campaign.deadline) <= now) return "付款已截止";
  if (campaign.core_locked_at) return "數量設定已鎖定";
  return "";
}
