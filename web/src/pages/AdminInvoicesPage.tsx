import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { OrderCategoryFilter } from "../components/OrderCategoryFilter";
import { orderCategoryLabels, type OrderCategory } from "../lib/order-category";
import { AdminNav } from "../components/AdminNav";
import { AdminManualInvoiceRecords } from "../components/AdminManualInvoiceRecords";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, downloadApiFile, formatMoney } from "../lib/api";
import { financeDateTime } from "../lib/meal-finance";
import { resolveInvoiceTax } from "../lib/invoice-tax";
import type { InvoiceTax } from "../lib/types";
import "./AdminRecordsPage.css";
const states: Record<string, string> = {not_eligible: "尚未符合開立條件", pending: "待開立", issued: "已開立", failed: "開立失敗", void_pending: "作廢處理中", voided: "已作廢"};
interface Invoice {sales_channel: string; order_id: string; order_number: string; email: string; created_at: string; invoice_number?: string; invoice_date?: string; status: string; payment_status: string; amount: number; tax_amount: number; estimated_tax_amount?: number; invoice_tax?: InvoiceTax | null; provider: string;}

function InvoiceTaxCell({ row }: { row: Invoice }) {
  const tax = resolveInvoiceTax({ invoiceTax: row.invoice_tax, invoiceStatus: row.status, taxAmount: row.estimated_tax_amount });
  return <><span>{tax.amountLabel}</span><br/>{tax.amount === null ? tax.amountText : formatMoney(tax.amount)}</>;
}

export function AdminInvoicesPage() {
  const {user, openLogin} = useAuth();
  const [q, setQ] = useState(""), [search, setSearch] = useState(""), [status, setStatus] = useState(""), [start, setStart] = useState(""), [end, setEnd] = useState(""), [offset, setOffset] = useState(0);
  const [category, setCategory] = useState<OrderCategory>("all");
  const params = new URLSearchParams({order_category: category, q: search, offset: String(offset), limit: "30"}); if (status) params.set("status", status); if (start) params.set("starts_on", start); if (end) params.set("ends_on", end);
  const data = useQuery({queryKey: ["invoice-register", params.toString()], queryFn: () => apiFetch<{items: Invoice[]; total: number}>(`/v1/admin/invoice-register?${params}`), enabled: user?.user_role === "admin"});
  const download = useMutation({mutationFn: () => downloadApiFile(`/v1/admin/invoice-register.csv?${params}`, "發票明細.csv")});
  if (user?.user_role !== "admin") return <section className="admin-gate"><h1>發票管理</h1>{!user && <button className="button button-primary" onClick={openLogin}>管理員登入</button>}</section>;
  return <section className="admin-module-page"><header className="workspace-heading"><div><p className="eyebrow">INVOICES / 發票管理</p><h1>發票查詢與明細</h1></div><Link className="button button-quiet" to="/admin" search={{section: "manual-invoice"}}>手動開票</Link></header><AdminNav/><div className="admin-records-content"><AdminManualInvoiceRecords/><OrderCategoryFilter value={category} onChange={value => {setCategory(value); setOffset(0);}}/><Link className="text-link" to="/admin/statements">匯入發票明細與對帳</Link><form className="admin-records-filters" onSubmit={event => {event.preventDefault(); setSearch(q); setOffset(0);}}><label className="field"><span>訂單編號、發票號碼或 Email</span><input maxLength={120} value={q} onChange={event => setQ(event.target.value)}/></label><label className="field"><span>發票狀態</span><select value={status} onChange={event => {setStatus(event.target.value); setOffset(0);}}><option value="">全部狀態</option>{Object.entries(states).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field"><span>訂單日起</span><input type="date" value={start} onChange={event => {setStart(event.target.value); setOffset(0);}}/></label><label className="field"><span>訂單日迄</span><input type="date" min={start} value={end} onChange={event => {setEnd(event.target.value); setOffset(0);}}/></label><button className="button button-primary">查詢</button><button className="button button-quiet" type="button" disabled={data.isPending || data.isError || download.isPending} onClick={() => download.mutate()}>匯出網站核對 CSV</button></form>{download.isError && <p role="alert">{download.error.message}</p>}
  {data.isPending ? <LoadingLines count={5}/> : data.isError ? <DataState title="發票無法讀取" detail={data.error.message}/> : <><p>{orderCategoryLabels[category]} · 共 {data.data.total} 筆</p><div className="admin-records-table" tabIndex={0} role="region" aria-label="發票明細"><table><thead><tr>{["訂單／Email", "發票號碼", "開立日期", "狀態", "含稅金額", "稅額", "操作"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{data.data.items.map(row => <tr key={row.order_id}><td>{row.order_number}<br/>{row.sales_channel === "meal_preorder" ? "便當訂單" : "一般訂單"}<br/>{row.email}</td><td>{row.invoice_number || "—"}</td><td>{row.invoice_date ? financeDateTime(row.invoice_date) : "—"}</td><td>{states[row.status] || row.status}</td><td>{formatMoney(row.amount)}</td><td><InvoiceTaxCell row={row}/></td><td><Link className="button button-quiet" to="/admin" search={{section: "orders", q: row.order_number, order_id: row.order_id}}>開啟訂單與發票</Link></td></tr>)}</tbody></table></div>{data.data.total > 30 && <nav className="admin-pagination" aria-label="發票分頁"><button className="button button-quiet" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一頁</button><span>{Math.floor(offset/30)+1}／{Math.ceil(data.data.total/30)}</span><button className="button button-quiet" disabled={offset+30 >= data.data.total} onClick={() => setOffset(offset+30)}>下一頁</button></nav>}</>}
  </div></section>;
}
