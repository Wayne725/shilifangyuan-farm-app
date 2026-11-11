import { DownloadSimple } from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, apiFetch, downloadApiFile, formatMoney } from "../lib/api";
import { DataState, LoadingLines } from "./Shared";

const taxNames = { taxable: "應稅", tax_exempt: "免稅" };
const membershipNames = { nonmember: "一般使用者", trainee: "實習社員", member: "社員" };
const channelNames = { regular: "日常選品", group: "共同購買", meal_preorder: "便當" };

interface TaxLedgerRow {
  tax_type: keyof typeof taxNames;
  membership_type: keyof typeof membershipNames;
  sales_channel: keyof typeof channelNames;
  sales_amount: number;
  tax_amount: number;
  order_count: number;
}

interface TaxLedgerPeriod {
  starts_on: string;
  ends_on: string;
}

interface TaxLedgerReport extends TaxLedgerPeriod {
  rows: TaxLedgerRow[];
}

function currentUtcPeriod(): TaxLedgerPeriod {
  const today = new Date().toISOString().slice(0, 10);
  return { starts_on: `${today.slice(0, 7)}-01`, ends_on: today };
}

export function TaxLedger({ adminId }: { adminId: string }) {
  const [draft, setDraft] = useState(currentUtcPeriod);
  const [period, setPeriod] = useState(draft);
  const [formError, setFormError] = useState("");
  const params = new URLSearchParams({ ...period });
  const report = useQuery({
    queryKey: ["admin-tax-ledger", adminId, period],
    queryFn: ({ signal }) => apiFetch<TaxLedgerReport>(`/v1/admin/finance/tax-ledger?${params}`, { signal, cache: "no-store" }),
    retry: (failures, error) => !(error instanceof ApiError && error.status < 500) && failures < 2,
  });
  const download = useMutation({
    mutationFn: () => downloadApiFile(
      `/v1/admin/finance/tax-ledger.csv?${params}`,
      `稅務彙總（預估）-${period.starts_on}-${period.ends_on}.csv`,
    ),
  });
  const accessError = download.error instanceof ApiError && download.error.status === 403 ? download.error : null;
  const reportError = report.error ?? accessError;

  return <section>
    <h2>稅務彙總</h2>
    <p>依 UTC 付款日彙總目前已付款訂單。銷售額採品項小計，稅額為估算。</p>
    <form className="meal-finance-filters tax-ledger-filters" onSubmit={(event) => {
      event.preventDefault();
      if (draft.starts_on > draft.ends_on) {
        setFormError("結束日期不可早於開始日期。");
        return;
      }
      setFormError("");
      download.reset();
      if (draft.starts_on === period.starts_on && draft.ends_on === period.ends_on) void report.refetch();
      else setPeriod(draft);
    }}>
      <label className="field"><span>付款日期起</span><input type="date" required value={draft.starts_on} onChange={(event) => setDraft({ ...draft, starts_on: event.target.value })} /></label>
      <label className="field"><span>付款日期迄</span><input type="date" required value={draft.ends_on} onChange={(event) => setDraft({ ...draft, ends_on: event.target.value })} /></label>
      <button className="button button-primary" type="submit" disabled={report.isFetching || download.isPending}>查詢</button>
    </form>
    {formError && <p className="form-error" role="alert">{formError}</p>}
    {report.isFetching ? <LoadingLines count={3} /> : reportError ? (
      <DataState kind="error" title="稅務彙總無法讀取" detail={reportError.message} />
    ) : report.data && <>
      <div className="meal-finance-result-heading">
        <p>{report.data.starts_on} 至 {report.data.ends_on}（UTC）</p>
        <button className="button button-system" type="button" disabled={download.isPending} onClick={() => download.mutate()}>
          <DownloadSimple size={18} />{download.isPending ? "下載中…" : "下載預估 CSV"}
        </button>
      </div>
      {download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
      {report.data.rows.length === 0 ? <p role="status">這段期間沒有已付款品項</p> :
        <div className="meal-finance-table-wrap" tabIndex={0} role="region" aria-label="稅務彙總明細">
          <table className="meal-finance-table" aria-label="稅務彙總明細">
            <thead><tr>
              {["稅別", "結帳身分", "銷售通路", "銷售額", "預估稅額", "訂單筆數"].map((name) => <th key={name} scope="col">{name}</th>)}
            </tr></thead>
            <tbody>{report.data.rows.map((row) => <tr key={`${row.tax_type}-${row.membership_type}-${row.sales_channel}`}>
              <td>{taxNames[row.tax_type]}</td>
              <td>{membershipNames[row.membership_type]}</td>
              <td>{channelNames[row.sales_channel]}</td>
              <td>{formatMoney(row.sales_amount)}</td>
              <td>{formatMoney(row.tax_amount)}</td>
              <td>{row.order_count}</td>
            </tr>)}</tbody>
          </table>
        </div>}
    </>}
  </section>;
}
