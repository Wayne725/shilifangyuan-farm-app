import { ApiError, apiFetch } from './api';
import type { PaymentAttempt } from './types';

export interface ActivityRefundRequest {
  id: string; activity_charge_id: string; payment_attempt_id: string; kind: string;
  status: 'pending_review' | 'approved' | 'rejected' | 'externally_refunded'; amount: number; reason: string;
  version: number; review_reason?: string | null; created_at: string; reviewed_at?: string | null;
}
export interface ActivityCharge {
  id: string; number: string; activity_id: string; registration_id: string; registration_revision: number;
  activity_title: string; contact_name: string; contact_email?: string | null; membership_type: string;
  amount: number; payment_status: string; expires_at: string; paid_at?: string | null; created_at: string;
  available_actions: string[]; refund_requests: ActivityRefundRequest[];
}
export interface ActivityRegistration {
  id: string; activity_id: string; activity_title?: string; starts_at?: string; ends_at?: string; location?: string; available_actions?: string[];
  revision: number; contact_name?: string; contact_email?: string | null; status: string; queue_position: number;
  registered_at?: string; cancelled_at?: string | null; checked_in_at?: string | null;
  charge?: ActivityCharge | null; refund_request?: ActivityRefundRequest | null;
}
export const activityAccessDenied = (error: unknown) => error instanceof ApiError && [401, 403].includes(error.status);
export const retryActivityRead = (count: number, error: Error) => !activityAccessDenied(error) && count < 1;
export const activityTime = (value?: string | null) => value ? new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`).getTime() : NaN;
export const activityDate = (value?: string | null) => value && Number.isFinite(activityTime(value)) ? new Intl.DateTimeFormat('zh-TW', {timeZone:'Asia/Taipei', year:'numeric', month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).format(activityTime(value)) : '—';
export const activityMembershipLabel = (type: string) => ({member:'社員',trainee:'見習社員',nonmember:'非社員'}[type] || '非社員');
export const activityPrice = (activity: {member_price?: number; non_member_price?: number}, type?: string) => ['member','trainee'].includes(type || '') ? activity.member_price || 0 : activity.non_member_price || 0;
export const activityRegistrationLabel = (status: string) => ({pending_payment:'待付款',registered:'報名成功',waitlisted:'候補中',cancelled:'已取消',attended:'已出席',no_show:'未出席'}[status] || status);
export const activityPaymentLabel = (status: string) => ({pending:'待付款',confirming:'付款核對中',paid:'已付款',expired:'已逾期',failed:'付款失敗',refund_pending:'退款處理中',refunded:'已退款',late_paid_refund_required:'晚到款待退款審核'}[status] || status);
export const activityRefundLabel = (status: string) => ({pending_review:'退款待審',approved:'退款已核准',rejected:'退款未核准',externally_refunded:'金流已退款'}[status] || status);
export function canPayActivity(charge: ActivityCharge, now = Date.now()) { return charge.available_actions.includes('pay') && activityTime(charge.expires_at) > now; }
export async function createActivityPayment(chargeId: string) { return apiFetch<PaymentAttempt>(`/v1/activity-charges/${chargeId}/payment-attempts`, {method:'POST'}); }
export function filterActivityRegistrations(items: ActivityRegistration[], q: string, status: string) {
  const term=q.trim().toLocaleLowerCase();
  return items.filter(item => (!status || item.status===status) && `${item.activity_title || ''} ${item.charge?.number || ''} ${item.contact_name || ''} ${item.contact_email || ''}`.toLocaleLowerCase().includes(term));
}
