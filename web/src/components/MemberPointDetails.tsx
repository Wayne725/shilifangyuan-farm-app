import { Certificate } from "@phosphor-icons/react";
import * as Tabs from "@radix-ui/react-tabs";
import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import type { MemberBadge, PointSummary, PointTransaction } from "../lib/types";
import { LoadingLines } from "./Shared";
import "./MemberPointDetails.css";

export type CooperationTab = "points" | "badges";

const sourceLabels: Record<string, string> = {
  purchase: "消費累積", refund: "退款扣回", wish_launched: "願望成案",
  activity: "活動參與", vote: "表決參與", admin_adjustment: "管理員調整",
};
const ledgerDate = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});
function formatLedgerDate(value: string): string {
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? "日期待核對" : ledgerDate.format(date);
}
function category(item: PointTransaction): "regular" | "meal" | "other" {
  if (!item.source_order) return "other";
  return item.source_order.sales_channel === "meal_preorder" ? "meal" : "regular";
}
const categoryLabels = { regular: "一般訂單", meal: "便當訂單", other: "其他紀錄" };

export function MemberPointDetails({ tab, onTabChange, points, badges }: {
  tab: CooperationTab;
  onTabChange: (tab: CooperationTab) => void;
  points: UseQueryResult<PointSummary>;
  badges: UseQueryResult<MemberBadge[]>;
}) {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const transactions = points.data?.transactions ?? [];
  const term = query.trim().toLocaleLowerCase();
  const visible = transactions.filter((item) => (
    (source === "all" || category(item) === source)
    && (!term || [item.note, sourceLabels[item.source_type] || "其他紀錄", item.source_order?.order_number, formatLedgerDate(item.created_at)]
      .some((value) => value?.toLocaleLowerCase().includes(term)))
  ));
  return (
    <Tabs.Root className="member-point-details" id="cooperation-details" value={tab} onValueChange={(value) => onTabChange(value as CooperationTab)}>
      <Tabs.List className="tab-list" aria-label="合作紀錄明細">
        <Tabs.Trigger id="cooperation-points-tab" value="points">合作點數明細</Tabs.Trigger>
        <Tabs.Trigger id="cooperation-badges-tab" value="badges">已獲徽章明細</Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content value="points">
        {points.isPending ? <LoadingLines count={3} /> : points.isError ? (
          <div className="member-detail-error" role="alert"><p>點數明細無法讀取</p><button type="button" className="button button-quiet" disabled={points.isFetching} onClick={() => void points.refetch()}>重新讀取點數</button></div>
        ) : (
          <>
            <div className="member-point-balance"><span>目前合作點數</span><strong>{points.data?.balance ?? 0} 點</strong></div>
            <div className="member-point-filters">
              <label className="field"><span>查詢點數紀錄</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="訂單編號、原因、日期" /></label>
              <label className="field"><span>紀錄分類</span><select aria-label="紀錄分類" value={source} onChange={(event) => setSource(event.target.value)}><option value="all">全部紀錄</option><option value="regular">一般訂單</option><option value="meal">便當訂單</option><option value="other">其他紀錄</option></select></label>
              <button type="button" className="button button-quiet" disabled={!query && source === "all"} onClick={() => { setQuery(""); setSource("all"); }}>清除查詢</button>
            </div>
            <p className="member-detail-count" role="status">共 {visible.length} 筆紀錄</p>
            {visible.length === 0 ? <p className="member-detail-empty">{transactions.length === 0 ? "目前沒有點數紀錄" : "沒有符合條件的點數紀錄"}</p> : (
              <ul className="member-point-list" aria-label="點數交易紀錄">
                {visible.map((item) => (
                  <li key={item.id}>
                    <div className="member-point-date"><time dateTime={item.created_at}>{formatLedgerDate(item.created_at)}</time><span>{categoryLabels[category(item)]}</span></div>
                    <div className="member-point-source"><strong>{sourceLabels[item.source_type] || "其他紀錄"}</strong>{item.note && <p>{item.note}</p>}
                      {item.source_order && (item.source_order.sales_channel === "meal_preorder"
                        ? <Link className="text-link" to="/meal-orders" search={{ order_id: item.source_order.id }}>查看訂單 {item.source_order.order_number}</Link>
                        : <Link className="text-link" to="/orders" search={{ order_id: item.source_order.id }}>查看訂單 {item.source_order.order_number}</Link>)}
                    </div>
                    <strong className={`member-point-amount ${item.amount < 0 ? "debit" : "credit"}`} aria-label={`${item.amount < 0 ? "扣除" : "增加"} ${Math.abs(item.amount)} 點`}>{item.amount > 0 ? "+" : ""}{item.amount} 點</strong>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Tabs.Content>
      <Tabs.Content value="badges">
        {badges.isPending ? <LoadingLines count={3} /> : badges.isError ? (
          <div className="member-detail-error" role="alert"><p>徽章明細無法讀取</p><button type="button" className="button button-quiet" disabled={badges.isFetching} onClick={() => void badges.refetch()}>重新讀取徽章</button></div>
        ) : !badges.data?.length ? <p className="member-detail-empty">目前沒有已獲徽章</p> : (
          <ul className="member-badge-list" aria-label="已獲徽章紀錄">{badges.data.map((badge) => <li key={badge.key}><Certificate size={26} weight="light" /><div><strong>{badge.name}</strong><p>{badge.description}</p><span>獲得日期（台灣）</span><time dateTime={badge.granted_at}>{formatLedgerDate(badge.granted_at)}</time></div></li>)}</ul>
        )}
      </Tabs.Content>
    </Tabs.Root>
  );
}
