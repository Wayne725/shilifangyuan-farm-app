import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, downloadApiFile } from "../lib/api";
import { financePeriodPreset } from "../lib/meal-finance";
import type { Supplier } from "../lib/types";
import "./SupplierOrderDemand.css";

export function SupplierOrderDemand({ suppliers }: { suppliers: Supplier[] }) {
  const [open, setOpen] = useState(false);
  return <details className="supplier-order-demand" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>供應者叫貨單</summary>
    {open && <SupplierOrderDemandForm suppliers={suppliers} />}
  </details>;
}

function SupplierOrderDemandForm({ suppliers }: { suppliers: Supplier[] }) {
  const [period, setPeriod] = useState(() => financePeriodPreset("month"));
  const [supplierId, setSupplierId] = useState("");
  const [arrival, setArrival] = useState("");
  const [validation, setValidation] = useState("");
  const download = useMutation({
    mutationFn: (scope: "supplier" | "review") => {
      const params = new URLSearchParams({ starts_on: period.startsOn, ends_on: period.endsOn, scope });
      if (scope === "supplier") {
        params.set("supplier_id", supplierId);
        params.set("expected_arrival_on", arrival);
      }
      const title = scope === "supplier" ? "叫貨參考" : "叫貨待核對";
      return downloadApiFile(`/v1/admin/supplier-order-demand.csv?${params}`, `${title}-${period.startsOn}-${period.endsOn}.csv`);
    },
  });
  if (download.error instanceof ApiError && [401, 403].includes(download.error.status)) {
    return <p className="form-error" role="alert">{download.error.message}</p>;
  }

  function run(scope: "supplier" | "review") {
    download.reset();
    if (!period.startsOn || !period.endsOn || period.startsOn > period.endsOn) {
      setValidation("請確認起訖日期，結束日期不可早於開始日期。");
      return;
    }
    setValidation("");
    download.mutate(scope);
  }

  return <form className="admin-master-form" onSubmit={(event) => { event.preventDefault(); run("supplier"); }}>
    <p>按台灣付款日與下單時的供應者歸屬匯出。範圍為目前已付款的一般商品及已成團單品。歷史無供應者快照、未指定供應者或有訂單級退款紀錄的品項另供核對。</p>
    <fieldset disabled={download.isPending} className="field-grid two-columns">
      <label className="field"><span>付款日期起</span><input type="date" required value={period.startsOn} onChange={(event) => setPeriod({ ...period, startsOn: event.target.value })} /></label>
      <label className="field"><span>付款日期迄</span><input type="date" required value={period.endsOn} onChange={(event) => setPeriod({ ...period, endsOn: event.target.value })} /></label>
      <label className="field"><span>供應者</span><select value={supplierId} required onChange={(event) => setSupplierId(event.target.value)}>
        <option value="">請選擇供應者</option>
        {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.business_name}{supplier.is_active ? "" : "（停用）"}</option>)}
      </select></label>
      <label className="field"><span>預計到貨日</span><input type="date" required value={arrival} onChange={(event) => setArrival(event.target.value)} /></label>
    </fieldset>
    {(validation || download.error) && <p className="form-error" role="alert">{validation || download.error?.message}</p>}
    <div className="form-actions">
      <button className="button button-primary" type="submit" disabled={download.isPending || !supplierId || !arrival}>下載叫貨參考 CSV</button>
      <button className="button button-quiet" type="button" disabled={download.isPending} onClick={() => run("review")}>下載待核對 CSV</button>
    </div>
    {download.isPending && <p role="status">下載中…</p>}
  </form>;
}
