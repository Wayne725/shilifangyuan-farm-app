import { useState } from "react";
import { Link, useLocation } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatMoney } from "../lib/api";
import { financeDateTime } from "../lib/meal-finance";
import { invoiceStatusLabel } from "../lib/labels";
import "../pages/AdminRecordsPage.css";

interface ManualInvoiceRecord {
  id: string;
  order_id: string;
  order_number: string;
  recorded_at: string;
  operator_name: string | null;
  action: "invoice.arranged" | "walk_in_sale.collected";
  sale_category?: "meal" | "product" | null;
  handling: "on_payment" | "pickup_day";
  amount: number;
  reason: string | null;
  invoice_status: string;
  invoice_number: string | null;
  invoice_date: string | null;
  requested_issue_after: string | null;
}

const pageSize = 5;
const historyAnchor = "manual-invoice-records";

function ManualInvoiceRecordsList({ paginate }: { paginate: boolean }) {
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);
  const records = useQuery({
    queryKey: ["admin-manual-invoice-records", user?.id, offset],
    queryFn: ({ signal }) => apiFetch<{ items: ManualInvoiceRecord[]; total: number }>(
      `/v1/admin/manual-invoice-records?limit=${pageSize}&offset=${offset}`, { signal },
    ),
    enabled: user?.user_role === "admin",
    retry: false,
  });

  return <div className="admin-manual-invoice-content">
    {(paginate || records.isError) && <div className="admin-manual-invoice-toolbar">
      {paginate && records.data && <span>共 {records.data.total} 筆</span>}
      <button className="button button-quiet" type="button" disabled={records.isFetching} onClick={() => void records.refetch()}>
        {records.isFetching ? "更新中…" : "更新紀錄"}
      </button>
    </div>}
    {records.isPending ? <p role="status">正在讀取手動開票紀錄…</p>
      : records.isError ? <p className="form-error" role="alert">{records.error.message}</p>
        : records.data.items.length === 0 ? <p role="status">尚無手動開票紀錄</p>
          : <>
            <div className="admin-records-table admin-manual-invoice-table" tabIndex={0} role="region" aria-label="手動開票紀錄明細">
              <table>
                <thead><tr><th>操作時間／人員</th><th>交易／開票安排</th><th>目前發票狀態</th><th>處理原因</th></tr></thead>
                <tbody>{records.data.items.map(row => <tr key={row.id}>
                  <td>{financeDateTime(row.recorded_at)}<br/>{row.operator_name || "未記錄姓名"}</td>
                  <td><Link className="text-link" to="/admin" search={{ section: "orders", q: row.order_number }}>{row.order_number}</Link><br/>
                    {formatMoney(row.amount)} · {row.action === "walk_in_sale.collected" ? "現場收款開票" : "手動安排開票"}<br/>
                    {row.action === "walk_in_sale.collected" && <>{row.sale_category === "meal" ? "便當" : row.sale_category === "product" ? "一般商品" : "未分類"}<br/></>}
                    {row.handling === "pickup_day" ? "取餐日開立" : "現在開立"}
                    {row.handling === "pickup_day" && row.requested_issue_after && <><br/>{financeDateTime(row.requested_issue_after)}</>}
                  </td>
                  <td>{invoiceStatusLabel(row.invoice_status)}{row.invoice_number && <><br/>{row.invoice_number}</>}
                    {row.invoice_date && <><br/>{financeDateTime(row.invoice_date)}</>}
                  </td>
                  <td>{row.reason || "—"}</td>
                </tr>)}</tbody>
              </table>
            </div>
            {paginate && records.data.total > pageSize && <nav className="admin-pagination" aria-label="手動開票紀錄分頁">
              <button className="button button-quiet" type="button" disabled={offset === 0 || records.isFetching} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一頁</button>
              <span>{Math.floor(offset / pageSize) + 1}／{Math.ceil(records.data.total / pageSize)}</span>
              <button className="button button-quiet" type="button" disabled={offset + pageSize >= records.data.total || records.isFetching} onClick={() => setOffset(offset + pageSize)}>下一頁</button>
            </nav>}
          </>}
  </div>;
}

export function RecentManualInvoiceRecords() {
  return <section className="admin-manual-invoice-recent" aria-labelledby="recent-manual-invoice-heading">
    <header className="admin-manual-invoice-recent-header">
      <h2 id="recent-manual-invoice-heading">近期開票紀錄</h2>
      <Link className="text-link" to="/admin/invoices" hash={historyAnchor}>查看完整紀錄</Link>
    </header>
    <ManualInvoiceRecordsList paginate={false} />
  </section>;
}

export function AdminManualInvoiceRecords() {
  const linked = useLocation({ select: location => location.hash === historyAnchor });
  const [open, setOpen] = useState(linked);
  return <details id={historyAnchor} className="admin-manual-invoice-records" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>手動開票紀錄</summary>
    {open && <ManualInvoiceRecordsList paginate />}
  </details>;
}
