import { ArrowRight } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link, linkOptions } from "@tanstack/react-router";

import { ApiError, apiFetch } from "../lib/api";
import { DataState, LoadingLines } from "./Shared";
import "./AdminToday.css";

interface TodayCounts {
  pending_admissions: number;
  pending_order_cancellations: number;
  unpaid_business_orders: number;
  today_business_cash_uncollected: number;
  pending_activity_refunds: number;
  active_activities: number;
  active_activity_registrations: number;
  active_campaigns: number;
  pending_campaign_decisions: number;
  voting_group_proposals: number;
  voting_member_proposals: number;
  invoice_followups: number;
}

const workItems = [
  { key: "pending_admissions", label: "待審入社申請", unit: "件", link: linkOptions({ to: "/admin/social", search: { tab: "reviews" } }) },
  { key: "pending_order_cancellations", label: "待審退單", unit: "筆", link: linkOptions({ to: "/admin", search: { section: "orders", queue: "refunds" } }) },
  { key: "unpaid_business_orders", label: "會餐未付款", unit: "筆", link: linkOptions({ to: "/admin", search: { section: "orders", queue: "business-unpaid" } }) },
  { key: "today_business_cash_uncollected", label: "今日會餐現金未收", unit: "筆", link: linkOptions({ to: "/admin/meals", search: { tab: "orders", queue: "today_business_cash_uncollected" } }) },
  { key: "pending_activity_refunds", label: "待審活動退款", unit: "筆", link: linkOptions({ to: "/admin/social", search: { tab: "activities", view: "refunds" } }) },
  { key: "invoice_followups", label: "發票待合作社處理", unit: "筆", link: linkOptions({ to: "/admin", search: { section: "orders", queue: "invoice" } }) },
  { key: "pending_campaign_decisions", label: "待確認成團", unit: "團", link: linkOptions({ to: "/admin/groups", search: { tab: "campaigns", view: "decision" } }) },
  { key: "active_activities", label: "進行中的活動", unit: "場", link: linkOptions({ to: "/admin/social", search: { tab: "activities", view: "active" } }) },
  { key: "active_campaigns", label: "進行中的共同購買", unit: "團", link: linkOptions({ to: "/admin/groups", search: { tab: "campaigns", view: "active" } }) },
  { key: "voting_group_proposals", label: "投票中的團購提案", unit: "件", link: linkOptions({ to: "/admin/groups", search: { tab: "proposals", view: "voting" } }) },
  { key: "voting_member_proposals", label: "表決中的社員提案", unit: "件", link: linkOptions({ to: "/admin/social", search: { tab: "proposals", view: "voting" } }) },
] as const;

export function AdminToday({ adminId }: { adminId: string }) {
  const counts = useQuery({
    queryKey: ["admin-today", adminId],
    queryFn: ({ signal }) => apiFetch<TodayCounts>("/v1/admin/today", { signal }),
    staleTime: 0,
    retry: (attempt, error) => !(error instanceof ApiError && [401, 403].includes(error.status)) && attempt < 1,
  });

  if (counts.isPending) return <LoadingLines count={4} />;
  if (counts.isError) {
    return <DataState kind="error" title="今日待辦無法讀取" detail={counts.error.message} onAction={() => counts.refetch()} />;
  }

  return <ul className="admin-today-grid" aria-label="今日待辦清單">
    {workItems.map(({ key, label, unit, link }) => <li key={key}>
      <Link {...link} className="admin-today-card">
        <span>{label}</span>
        <span className="admin-today-count"><strong>{counts.data[key] ?? "—"}</strong> {unit}</span>
        {key === "active_activities" && <small>共 {counts.data.active_activity_registrations} 人次報名</small>}
        <span className="admin-today-arrow" aria-hidden="true"><ArrowRight size={20} /></span>
      </Link>
    </li>)}
  </ul>;
}
