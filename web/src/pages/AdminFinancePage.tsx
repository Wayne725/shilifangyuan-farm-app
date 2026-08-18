import * as Tabs from "@radix-ui/react-tabs";
import { Buildings, DownloadSimple, Gauge, HandCoins, Receipt, Warning } from "@phosphor-icons/react";
import { useMutation, useQueries } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, downloadApiFile, formatMoney } from "../lib/api";
import type { SalesBreakdownReport, SurplusPreview, TaxLedgerReport } from "../lib/types";

interface AdminMembership {
  id: string;
  user_id: string;
  status: string;
  member_number?: string | null;
  trainee_number?: string | null;
}

interface SurplusRequest {
  label: string;
  starts_on: string;
  ends_on: string;
  total_cost: number;
  reserve_percentage: number;
}

const currentYear = new Date().getFullYear();

export function AdminFinancePage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const [startsOn, setStartsOn] = useState(`${currentYear}-01-01`);
  const [endsOn, setEndsOn] = useState(`${currentYear}-12-31`);
  const periodValid = startsOn.length === 10 && endsOn.length === 10 && startsOn <= endsOn;
  const query = `starts_on=${startsOn}&ends_on=${endsOn}`;
  const [sales, tax, memberships] = useQueries({ queries: [
    { queryKey: ["nonmember-sales", startsOn, endsOn], queryFn: () => apiFetch<SalesBreakdownReport>(`/v1/admin/finance/nonmember-sales?${query}`), enabled: isAdmin && periodValid },
    { queryKey: ["tax-ledger", startsOn, endsOn], queryFn: () => apiFetch<TaxLedgerReport>(`/v1/admin/finance/tax-ledger?${query}`), enabled: isAdmin && periodValid },
    { queryKey: ["admin-members"], queryFn: () => apiFetch<AdminMembership[]>("/v1/admin/members"), enabled: isAdmin },
  ] });

  if (!isAdmin) return <section className="admin-gate"><div className="admin-gate-mark"><Buildings size={38} weight="light" /></div><p className="eyebrow">FINANCE OPERATIONS</p><h1>財務報表只向管理者開放。</h1>{!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/">返回首頁</Link>}</section>;

  return (
    <section className="admin-module-page finance-page">
      <header className="workspace-heading"><div><p className="eyebrow">FINANCE OPERATIONS / 財務制度</p><h1>銷售占比、稅務與年度結餘</h1></div><p>只統計已付款資料；占比超標會警示，但不會自行阻擋交易。</p></header>
      <AdminNav />
      <div className="finance-period"><label className="field"><span>開始日期</span><input type="date" value={startsOn} onChange={(event) => setStartsOn(event.target.value)} /></label><span>至</span><label className="field"><span>結束日期</span><input type="date" value={endsOn} onChange={(event) => setEndsOn(event.target.value)} /></label></div>
      {!periodValid && <p className="form-error">請選擇有效的查詢期間。</p>}
      <Tabs.Root className="admin-social-tabs" defaultValue="sales">
        <Tabs.List className="tab-list" aria-label="財務報表分類"><Tabs.Trigger value="sales">銷售占比</Tabs.Trigger><Tabs.Trigger value="tax">稅務分類帳</Tabs.Trigger><Tabs.Trigger value="surplus">結餘分配</Tabs.Trigger><Tabs.Trigger value="points">點數調整</Tabs.Trigger></Tabs.List>
        <Tabs.Content className="tab-content" value="sales"><SalesPanel report={sales.data} pending={sales.isPending} error={sales.error?.message} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="tax"><TaxPanel report={tax.data} pending={tax.isPending} error={tax.error?.message} query={query} startsOn={startsOn} endsOn={endsOn} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="surplus"><SurplusPanel startsOn={startsOn} endsOn={endsOn} periodValid={periodValid} /></Tabs.Content>
        <Tabs.Content className="tab-content" value="points"><PointsPanel memberships={memberships.data || []} /></Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

function SalesPanel({ report, pending, error }: { report?: SalesBreakdownReport; pending: boolean; error?: string }) {
  if (pending) return <LoadingLines count={4} />;
  if (error || !report) return <DataState kind="error" title="銷售占比無法讀取" detail={error || "沒有報表資料"} />;
  const rows = [
    ["一般買家", report.nonmember_revenue, report.nonmember_ratio],
    ["實習社員", report.trainee_revenue, report.trainee_ratio],
    ["正式社員", report.member_revenue, report.member_ratio],
  ] as const;
  return <section className="finance-section"><div className="finance-alert"><Gauge size={27} weight="light" /><div><span className={`status-chip ${report.level === "normal" ? "passed" : ""}`}>{report.level === "normal" ? "比例正常" : report.level === "warning" ? "25% 預警" : "30% 警示"}</span><h2>一般買家銷售占比 {(report.nonmember_ratio * 100).toFixed(2)}%</h2><p>法定警示線 30%；目前尚有 {formatMoney(report.headroom_amount)} 緩衝。</p></div></div><div className="finance-stat-grid">{rows.map(([label, revenue, ratio]) => <article key={label}><span>{label}</span><strong>{formatMoney(revenue)}</strong><b>{(ratio * 100).toFixed(2)}%</b><progress max={1} value={ratio} /></article>)}</div><div className="finance-total"><span>期間商品營收</span><strong>{formatMoney(report.total_revenue)}</strong></div>{report.level !== "normal" && <p className="finance-warning"><Warning size={18} />此為管理警示，不會自行阻擋一般買家交易。</p>}</section>;
}

function TaxPanel({ report, pending, error, query, startsOn, endsOn }: { report?: TaxLedgerReport; pending: boolean; error?: string; query: string; startsOn: string; endsOn: string }) {
  const download = useMutation({ mutationFn: () => downloadApiFile(`/v1/admin/finance/tax-ledger.csv?${query}`, `tax-ledger-${startsOn}-${endsOn}.csv`) });
  if (pending) return <LoadingLines count={4} />;
  if (error || !report) return <DataState kind="error" title="稅務分類帳無法讀取" detail={error || "沒有報表資料"} />;
  return <section className="finance-section"><div className="section-title-row"><div><p className="eyebrow">TAX LEDGER</p><h2>稅務分類帳</h2></div><button className="button button-system" type="button" disabled={download.isPending} onClick={() => download.mutate()}><DownloadSimple size={17} />下載 CSV</button></div>{report.rows.length === 0 ? <DataState title="期間內沒有已付款交易" detail="調整日期後重新查看。" /> : <div className="finance-ledger"><div className="finance-ledger-head"><span>稅別／身分／通路</span><span>銷售額</span><span>稅額</span><span>訂單</span></div>{report.rows.map((row, index) => <div key={`${row.tax_type}-${row.membership_type}-${row.sales_channel}-${index}`}><strong>{taxLabel(row.tax_type)} · {membershipLabel(row.membership_type)} · {channelLabel(row.sales_channel)}</strong><span>{formatMoney(row.sales_amount)}</span><span>{formatMoney(row.tax_amount)}</span><span>{row.order_count} 筆</span></div>)}</div>}{download.isError && <p className="form-error">{download.error.message}</p>}</section>;
}

function SurplusPanel({ startsOn, endsOn, periodValid }: { startsOn: string; endsOn: string; periodValid: boolean }) {
  const [label, setLabel] = useState(`${currentYear} 年度`);
  const [totalCost, setTotalCost] = useState(0);
  const [reserve, setReserve] = useState(50);
  const [confirmed, setConfirmed] = useState(false);
  const [previewRequest, setPreviewRequest] = useState<SurplusRequest | null>(null);
  const body = useMemo<SurplusRequest>(() => ({ label: label.trim(), starts_on: startsOn, ends_on: endsOn, total_cost: totalCost, reserve_percentage: reserve }), [endsOn, label, reserve, startsOn, totalCost]);
  const dryRun = useMutation({ mutationFn: (payload: SurplusRequest) => apiFetch<SurplusPreview>("/v1/admin/surplus/dry-run", { method: "POST", body: JSON.stringify(payload) }), onSuccess: (_data, payload) => { setPreviewRequest(payload); setConfirmed(false); } });
  const confirm = useMutation({ mutationFn: (payload: SurplusRequest) => apiFetch<SurplusPreview>("/v1/admin/surplus/confirm", { method: "POST", body: JSON.stringify(payload) }), onSuccess: () => setConfirmed(true) });
  const valid = periodValid && Boolean(label.trim()) && totalCost >= 0 && reserve >= 0 && reserve <= 100;
  const previewMatches = previewRequest !== null && JSON.stringify(previewRequest) === JSON.stringify(body);
  return <section className="finance-section"><div className="form-heading"><div><p className="eyebrow">SURPLUS ALLOCATION</p><h2>年度結餘分配</h2></div><span>必須先試算；正式確認後會建立稽核紀錄。</span></div><div className="field-grid three-columns"><label className="field"><span>會計年度名稱</span><input value={label} onChange={(event) => setLabel(event.target.value)} /></label><label className="field"><span>總成本</span><input min={0} type="number" value={totalCost} onChange={(event) => setTotalCost(Number(event.target.value))} /></label><label className="field"><span>公積金比例</span><input min={0} max={100} type="number" value={reserve} onChange={(event) => setReserve(Number(event.target.value))} /></label></div><button className="button button-quiet" type="button" disabled={!valid || dryRun.isPending} onClick={() => dryRun.mutate(body)}><Receipt size={17} />試算，不寫入</button>{dryRun.data && <div className="surplus-preview"><div className="surplus-totals"><span><small>期間營收</small><strong>{formatMoney(dryRun.data.total_revenue)}</strong></span><span><small>總結餘</small><strong>{formatMoney(dryRun.data.total_surplus)}</strong></span><span><small>公積金</small><strong>{formatMoney(dryRun.data.reserve_amount)}</strong></span><span><small>可分配</small><strong>{formatMoney(dryRun.data.distributable_surplus)}</strong></span></div><div className="surplus-distributions">{dryRun.data.distributions.map((item) => <p key={item.member_id}><strong>{item.member_name}</strong><span>貢獻 {formatMoney(item.contribution_amount)}</span><b>{formatMoney(item.distribution_amount)}</b></p>)}</div>{confirmed ? <p className="form-success">年度結餘已確認撥付，稽核紀錄已建立。</p> : previewMatches && previewRequest ? <button className="button button-danger" type="button" disabled={confirm.isPending} onClick={() => confirm.mutate(previewRequest)}><HandCoins size={18} />確認撥付（不可逆）</button> : <p className="form-error">設定已變更，請重新試算後再確認撥付。</p>}</div>}{(dryRun.isError || confirm.isError) && <p className="form-error">{dryRun.error?.message || confirm.error?.message}</p>}</section>;
}

function PointsPanel({ memberships }: { memberships: AdminMembership[] }) {
  const [userId, setUserId] = useState("");
  const [amount, setAmount] = useState(0);
  const [reason, setReason] = useState("");
  const adjust = useMutation({ mutationFn: () => apiFetch("/v1/admin/points/adjustments", { method: "POST", body: JSON.stringify({ user_id: userId, amount, reason }) }), onSuccess: () => { setAmount(0); setReason(""); } });
  return <form className="finance-section social-form" onSubmit={(event) => { event.preventDefault(); adjust.mutate(); }}><div className="form-heading"><div><p className="eyebrow">POINT ADJUSTMENT</p><h2>社員點數調整</h2></div><span>每筆調整都會記錄管理者與原因。</span></div><div className="field-grid three-columns"><label className="field"><span>社員</span><select required value={userId} onChange={(event) => setUserId(event.target.value)}><option value="">選擇社員</option>{memberships.map((membership) => <option key={membership.id} value={membership.user_id}>{membership.member_number || membership.trainee_number || membership.user_id} · {membership.status}</option>)}</select></label><label className="field"><span>調整點數</span><input required type="number" value={amount} onChange={(event) => setAmount(Number(event.target.value))} /></label><label className="field"><span>調整原因</span><input required value={reason} onChange={(event) => setReason(event.target.value)} /></label></div><div className="form-actions"><button className="button button-primary" disabled={!userId || amount === 0 || !reason.trim() || adjust.isPending}>確認調整</button></div>{adjust.isSuccess && <p className="form-success">點數調整已建立。</p>}{adjust.isError && <p className="form-error">{adjust.error.message}</p>}</form>;
}

function taxLabel(value: string) { return value === "taxable" ? "應稅" : "免稅"; }
function membershipLabel(value: string) { return { nonmember: "一般買家", trainee: "實習社員", member: "正式社員" }[value] || value; }
function channelLabel(value: string) { return { regular: "一般商品", group: "共同購買", meal_preorder: "便當預購" }[value] || value; }
