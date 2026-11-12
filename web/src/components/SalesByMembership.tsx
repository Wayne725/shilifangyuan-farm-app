import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { currentUtcFinancePeriod, type UtcFinancePeriod } from "../lib/meal-finance";
import { DataState, LoadingLines } from "./Shared";

const memberships = { nonmember: "一般買家", trainee: "實習社員", member: "社員" };
const percentage = new Intl.NumberFormat("zh-TW", { style: "percent", maximumFractionDigits: 1 });

interface SalesByMembershipReport extends UtcFinancePeriod {
  total_revenue: number;
  monitoring_threshold_reached: number | null;
  sales_breakdown: Array<{ membership_type: keyof typeof memberships; revenue: number; ratio: number }>;
}

export function SalesByMembership({ adminId }: { adminId: string }) {
  const [draft, setDraft] = useState(currentUtcFinancePeriod);
  const [period, setPeriod] = useState(draft);
  const [validation, setValidation] = useState("");
  const params = new URLSearchParams({ ...period });
  const report = useQuery({
    queryKey: ["admin-sales-by-membership", adminId, period],
    queryFn: ({ signal }) => apiFetch<SalesByMembershipReport>(
      `/v1/admin/finance/sales-by-membership?${params}`, { signal, cache: "no-store" },
    ),
    retry: (failures, error) => !(error instanceof ApiError && error.status < 500) && failures < 2,
  });

  return <section>
    <h2>身分銷售占比</h2>
    <p>按 UTC 付款日與結帳身分統計目前已付款訂單的品項小計，供營運觀察。</p>
    <form className="meal-finance-filters finance-period-filters" onSubmit={(event) => {
      event.preventDefault();
      if (draft.starts_on > draft.ends_on) {
        setValidation("結束日期不可早於開始日期。");
        return;
      }
      setValidation("");
      if (draft.starts_on === period.starts_on && draft.ends_on === period.ends_on) void report.refetch();
      else setPeriod(draft);
    }}>
      <label className="field"><span>付款日期起</span><input type="date" required value={draft.starts_on} onChange={(event) => setDraft({ ...draft, starts_on: event.target.value })} /></label>
      <label className="field"><span>付款日期迄</span><input type="date" required value={draft.ends_on} onChange={(event) => setDraft({ ...draft, ends_on: event.target.value })} /></label>
      <button type="submit" className="button button-primary" disabled={report.isFetching}>查詢</button>
    </form>
    {validation && <p className="form-error" role="alert">{validation}</p>}
    {report.isFetching ? <LoadingLines count={3} /> : report.isError ? (
      <DataState kind="error" title="銷售占比無法讀取" detail={report.error.message} />
    ) : report.data && <>
      <div className="meal-finance-result-heading">
        <p>{report.data.starts_on} 至 {report.data.ends_on}（UTC）</p>
        {report.data.monitoring_threshold_reached !== null && <p role="status">一般買家占比達營運提醒值 {percentage.format(report.data.monitoring_threshold_reached)}</p>}
      </div>
      <div className="meal-finance-table-wrap">
        <table className="meal-finance-table" aria-label="結帳身分銷售占比">
          <thead><tr><th scope="col">結帳身分</th><th scope="col">銷售額</th><th scope="col">占比</th></tr></thead>
          <tbody>{report.data.sales_breakdown.map((row) => <tr key={row.membership_type}>
            <th scope="row">{memberships[row.membership_type]}</th>
            <td>{formatMoney(row.revenue)}</td>
            <td>{report.data.total_revenue > 0 ? percentage.format(row.ratio) : "—"}</td>
          </tr>)}</tbody>
          <tfoot><tr><th scope="row">合計</th><td>{formatMoney(report.data.total_revenue)}</td><td /></tr></tfoot>
        </table>
      </div>
    </>}
  </section>;
}
