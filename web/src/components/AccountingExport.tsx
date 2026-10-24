import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch, downloadApiFile } from "../lib/api";
import { OrderCategoryFilter } from "./OrderCategoryFilter";
import { type OrderCategory } from "../lib/order-category";
import { financePeriodPreset } from "../lib/meal-finance";
import "./AccountingExport.css";

const formats = {
  invoice: {label: "發票明細 XLSX", extension: ".xlsx"},
  newebpay: {label: "藍新金流 CSV", extension: ".csv"},
  jkopay: {label: "街口支付 XLSX", extension: ".xlsx"},
  linepay: {label: "LINE Pay XLSX", extension: ".xlsx"},
  esun: {label: "玉山銀行 XLS", extension: ".xls"},
};
interface Batch {id: string; filename: string; created_at: string;}

export function AccountingExport({initialFormat = "invoice"}: {initialFormat?: keyof typeof formats}) {
  const [format, setFormat] = useState<keyof typeof formats>(initialFormat);
  const [period, setPeriod] = useState(() => financePeriodPreset("month"));
  const [category, setCategory] = useState<OrderCategory>("all");
  const [batchId, setBatchId] = useState("");
  const [page, setPage] = useState(0);
  const [message, setMessage] = useState("");
  const batches = useQuery({queryKey: ["accounting-export-batches", format, page], queryFn: () => apiFetch<{items: Batch[]; total: number}>(`/v1/admin/financial-statements?source=${format}&limit=20&offset=${page * 20}`)});
  const download = useMutation({mutationFn: () => {
    const parameters = new URLSearchParams({order_category: category, source: format, starts_on: period.startsOn, ends_on: period.endsOn});
    if (batchId) parameters.set("batch_id", batchId);
    return downloadApiFile(`/v1/admin/financial-statements/accounting-export?${parameters}`, `${formats[format].label.replace(/ (CSV|XLSX|XLS)$/, "")}-${period.startsOn}-${period.endsOn}${formats[format].extension}`);
  }, onSuccess: () => setMessage("匯出完成，已開始下載。")});
  useEffect(() => {setMessage(""); download.reset();}, [format, period.startsOn, period.endsOn, batchId, category]); // eslint-disable-line react-hooks/exhaustive-deps
  return <section className="accounting-export" aria-labelledby="accounting-export-heading">
    <header><h2 id="accounting-export-heading">會計格式匯出</h2><Link className="text-link" to="/admin/statements">匯入明細與對帳</Link></header>
    <OrderCategoryFilter value={category} onChange={setCategory} />
    <form onSubmit={event => {event.preventDefault(); setMessage(""); download.mutate();}}>
      <label className="field"><span>匯出格式</span><select aria-label="匯出格式" value={format} onChange={event => {setFormat(event.target.value as keyof typeof formats); setBatchId(""); setPage(0);}}>{Object.entries(formats).map(([value, option]) => <option key={value} value={value}>{option.label}</option>)}</select></label>
      <label className="field"><span>{format === "invoice" ? "發票日期起" : "交易日期起"}</span><input required type="date" value={period.startsOn} onChange={event => setPeriod({...period, startsOn: event.target.value})}/></label>
      <label className="field"><span>{format === "invoice" ? "發票日期迄" : "交易日期迄"}</span><input required type="date" min={period.startsOn} value={period.endsOn} onChange={event => setPeriod({...period, endsOn: event.target.value})}/></label>
      <label className="field accounting-export-source"><span>資料來源</span><select aria-label="資料來源" required={format !== "invoice"} value={batchId} onChange={event => setBatchId(event.target.value)}><option value="">{format === "invoice" ? "網站發票紀錄" : "請選擇已匯入的明細檔"}</option>{batches.data?.items.map(batch => <option key={batch.id} value={batch.id}>{batch.filename}</option>)}</select></label>
      <button className="button button-primary" disabled={download.isPending || (!batchId && format !== "invoice")}>{download.isPending ? "匯出中…" : "匯出會計檔案"}</button>
    </form>
    {batches.data && batches.data.total > 20 && <nav className="accounting-export-pages" aria-label="會計明細檔分頁"><button className="button button-quiet" disabled={!page} onClick={() => {setPage(page - 1); setBatchId("");}}>上一頁檔案</button><span>{page + 1}／{Math.ceil(batches.data.total / 20)}</span><button className="button button-quiet" disabled={(page + 1) * 20 >= batches.data.total} onClick={() => {setPage(page + 1); setBatchId("");}}>下一頁檔案</button></nav>}
    {format !== "invoice" && batches.data?.total === 0 && <p role="status">尚未匯入此來源的實際交易明細。<Link to="/admin/statements">匯入明細</Link></p>}
    {batches.isError && <p className="form-error" role="alert">{batches.error.message}</p>}
    {download.isError && <p className="form-error" role="alert">{download.error.message}</p>}
    {message && <p role="status">{message}</p>}
  </section>;
}
