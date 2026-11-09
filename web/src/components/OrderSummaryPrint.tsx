import * as Dialog from "@radix-ui/react-dialog";
import { Printer, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import type { AdminPanelAccess } from "../lib/admin-panel-access";
import { orderSummaryParameters, type OrderSummaryFilters } from "../lib/order-summary";
import "./OrderSummaryPrint.css";

type PrintFilters = ReturnType<typeof orderSummaryParameters>;
type Totals = { order_count: number; quantity: number; product_count: number };
type Preview = { fingerprint: string; summary: Totals; pages: string[]; page_count: number; filters: PrintFilters; mode: string };
type Attempt = { request_id: string; fingerprint: string; filters: PrintFilters };
type Batch = { request_id: string; fingerprint: string; mode: string; created_at: string; filters: PrintFilters; summary: Totals; pages: Array<{ id: string; page: number; status: string; last_error: string | null }> };
const labels: Record<string, string> = { pending: "等待發送", sending: "正在發送", submitted: "雲端已接受", printed: "已確認列印", failed: "出單失敗", unknown: "結果待確認", simulated: "模擬完成" };
const isSettled = (batch: Batch) => batch.pages.every((page) => ["printed", "failed", "simulated"].includes(page.status));
const canStartAnother = (batch: Batch) => batch.pages.every((page) => !["pending", "sending"].includes(page.status));
const errorLabels: Record<string, string> = {
  summary_changed_before_send: "訂單資料已變更，這張未送印；請核對已印頁數後重新查詢。",
  admin_has_not_seen_paper: "管理員尚未收到紙本，結果待確認。",
  printer_configuration_changed: "出單設備或模式已變更，請重新預覽後列印。",
};

function readAttempt(key: string): Attempt | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null") as Attempt | null;
    return value && typeof value.request_id === "string" && typeof value.fingerprint === "string" && value.filters?.starts_on ? value : null;
  } catch { return null; }
}

function BatchResult({ batch }: { batch: Batch }) {
  return <div className="summary-print-result" role="status">
    <strong>{batch.mode === "dry_run" ? "模擬列印" : "彙總單列印紀錄"} · {batch.summary.order_count} 筆訂單 · {batch.pages.length} 張</strong>
    <span>{batch.filters.date_basis === "pickup" ? "取餐" : "下單"} {batch.filters.starts_on} 至 {batch.filters.ends_on}</span>
    <small>批次 {batch.request_id}</small>
    <ul>{batch.pages.map((page) => <li key={page.id}><span>第 {page.page} 張</span><strong>{labels[page.status] || page.status}</strong>{page.last_error && <span className="form-error">{errorLabels[page.last_error] || page.last_error}</span>}</li>)}</ul>
  </div>;
}

export function OrderSummaryPrint({ filters, userId, disabled, access }: { filters: OrderSummaryFilters; userId: string; disabled?: boolean; access: AdminPanelAccess }) {
  const storageKey = `order-summary-print:${userId}`;
  const [attempt, setAttempt] = useState<Attempt | null>(() => readAttempt(storageKey));
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<Batch[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [reviewedPaper, setReviewedPaper] = useState(false);
  const [resolvePage, setResolvePage] = useState("");
  const [resolveReason, setResolveReason] = useState("");
  const busyRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const saveAttempt = (value: Attempt | null) => {
    setAttempt(value);
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
  };
  const operate = async (action: () => Promise<void>) => {
    if (access.isDenied() || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (failure) { if (mounted.current && !access.isDenied()) setError(failure instanceof Error ? failure.message : "無法完成操作，請再試一次。"); }
    finally { busyRef.current = false; if (mounted.current && !access.isDenied()) setBusy(false); }
  };
  const loadPreview = () => operate(async () => {
    setPreview(null); setResolvePage(""); setResolveReason(""); setReviewedPaper(false);
    const params = { ...orderSummaryParameters(filters), status: "paid" };
    const value = await access.request<Preview>(`/v1/admin/order-summary/print-preview?${new URLSearchParams(params)}`);
    if (mounted.current && !access.isDenied()) { setPreview(value); setBatch(null); setReviewedPaper(false); }
  });
  const findBatch = () => operate(async () => {
    if (!attempt) return;
    try {
      const value = await access.request<Batch>(`/v1/admin/order-summary/print-jobs/${attempt.request_id}`);
      if (mounted.current && !access.isDenied()) setBatch(value);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 404) throw new Error("尚未查到這筆送印，可重試同一筆送印；系統會沿用原識別碼。");
      throw failure;
    }
  });
  const send = () => operate(async () => {
    const value = attempt || (preview ? { request_id: crypto.randomUUID(), fingerprint: preview.fingerprint, filters: preview.filters } : null);
    if (!value) return;
    // Persist before sending so a lost response or reload reuses the same request.
    saveAttempt(value);
    try {
      const result = await access.request<Batch>("/v1/admin/order-summary/print-jobs", { method: "POST", body: JSON.stringify(value) });
      if (mounted.current && !access.isDenied()) { setBatch(result); setPreview(null); }
    } catch (failure) {
      if (failure instanceof ApiError && [400, 409, 422].includes(failure.status)) {
        if (mounted.current && !access.isDenied()) { saveAttempt(null); setPreview(null); }
      }
      throw failure;
    }
  });
  const loadHistory = async () => {
    if (access.isDenied()) return;
    setHistoryError("");
    try {
      const result = await access.request<{ items: Batch[] }>("/v1/admin/order-summary/print-jobs?limit=10");
      if (mounted.current && !access.isDenied()) setHistory(result.items);
    } catch (failure) { if (mounted.current && !access.isDenied()) setHistoryError(failure instanceof Error ? failure.message : "無法讀取列印紀錄。"); }
  };
  const resolve = (printed: boolean) => operate(async () => {
    if (!batch || !resolvePage) return;
    const value = await access.request<Batch>(`/v1/admin/order-summary/print-jobs/${batch.request_id}/pages/${resolvePage}/resolve`, {
      method: "POST", body: JSON.stringify({ printed, reason: resolveReason.trim() }),
    });
    if (mounted.current && !access.isDenied()) { setBatch(value); setResolvePage(""); setResolveReason(""); }
  });

  useEffect(() => {
    if (access.isDenied() || !open || !batch || isSettled(batch)) return;
    let active = true;
    const timer = setInterval(() => {
      if (access.isDenied()) return;
      void access.request<Batch>(`/v1/admin/order-summary/print-jobs/${batch.request_id}`)
        .then((value) => { if (active && !access.isDenied()) setBatch(value); })
        .catch(() => { /* Keep the last confirmed state; manual refresh reports errors. */ });
    }, 4000);
    return () => { active = false; clearInterval(timer); };
  }, [open, batch?.request_id, batch && isSettled(batch), access.denied, access.isDenied, access.request]);

  const show = (value: boolean) => {
    if (access.isDenied() || busyRef.current) return;
    setOpen(value);
    if (value) {
      setError("");
      if (attempt) void findBatch();
      else void loadPreview();
    }
  };

  if (access.denied) return null;
  return <section className="order-summary-print" aria-label="彙總列印">
    <Dialog.Root open={open} onOpenChange={show}>
      <Dialog.Trigger asChild><button type="button" className="button button-primary" disabled={disabled && !attempt}><Printer size={20} />{attempt ? "查看彙總送印結果" : "列印彙總單"}</button></Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="summary-print-overlay" />
        <Dialog.Content className="summary-print-dialog" aria-describedby={undefined} onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }} onPointerDownOutside={(event) => { if (busy) event.preventDefault(); }}>
          <Dialog.Title>彙總單列印</Dialog.Title>
          {preview && !attempt && <>
            <p className="summary-print-scope">{preview.filters.order_category === "meal" ? "便當訂單" : "一般訂單"} · {preview.filters.date_basis === "pickup" ? "取餐日期" : "下單日期"}<br />{preview.filters.starts_on} 至 {preview.filters.ends_on}<br /><strong>只印已付款且未取消</strong></p>
            <p>{preview.summary.order_count} 筆訂單 · {preview.summary.quantity} 份／件 · {preview.summary.product_count} 種品項 · {preview.page_count} 張</p>
            {preview.mode === "dry_run" && <p role="status">目前為模擬模式，不會送到出單機。</p>}
            <div className="summary-print-previews">{preview.pages.map((page, index) => <section key={index} aria-label={`第 ${index + 1} 張預覽`}><h3>第 {index + 1} 張</h3><pre>{page}</pre></section>)}</div>
            <button type="button" className="button button-primary" disabled={busy} onClick={() => void send()}>{busy ? "送印中…" : `${preview.mode === "dry_run" ? "確認模擬" : "確認列印"} ${preview.page_count} 張`}</button>
          </>}
          {batch && <BatchResult batch={batch} />}
          {batch?.mode === "live" && batch.pages.some((page) => ["submitted", "unknown"].includes(page.status)) && <details className="summary-print-manual">
            <summary>人工核對紙本</summary>
            <label className="field"><span>核對頁次</span><select aria-label="核對頁次" value={resolvePage} onChange={(event) => setResolvePage(event.target.value)}><option value="">選擇頁次</option>{batch.pages.filter((page) => ["submitted", "unknown"].includes(page.status)).map((page) => <option key={page.id} value={page.id}>第 {page.page} 張 · {labels[page.status]}</option>)}</select></label>
            <label className="field"><span>核對紀錄</span><input value={resolveReason} maxLength={200} onChange={(event) => setResolveReason(event.target.value)} /></label>
            <div className="summary-print-actions"><button type="button" className="button button-primary" disabled={busy || !resolvePage} onClick={() => void resolve(true)}>確認已收到此張</button><button type="button" className="button button-quiet" disabled={busy || !resolvePage} onClick={() => void resolve(false)}>記錄尚未收到</button></div>
          </details>}
          {attempt && !batch && <p role="status">正在核對這筆送印是否已建立。批次 {attempt.request_id}</p>}
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="summary-print-actions">
            {attempt && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void findBatch()}>查詢送印結果</button>}
            {attempt && !batch && <button type="button" className="button button-primary" disabled={busy} onClick={() => void send()}>重試同一筆送印</button>}
            {!attempt && !preview && <button type="button" className="button button-primary" disabled={busy} onClick={() => void loadPreview()}>{busy ? "讀取預覽中…" : "重新產生預覽"}</button>}
          </div>
          {batch && canStartAnother(batch) && <div className="summary-print-new">
            <label><input type="checkbox" checked={reviewedPaper} onChange={(event) => setReviewedPaper(event.target.checked)} />{isSettled(batch) ? "已核對紙本，需要另印一份彙總單" : "已核對紙本，接受可能重複列印並另印一份"}</label>
            <button type="button" className="button button-quiet" disabled={busy || !reviewedPaper} onClick={() => { saveAttempt(null); setBatch(null); void loadPreview(); }}>建立另一份彙總單</button>
          </div>}
          <Dialog.Close asChild><button type="button" className="icon-button summary-print-close" disabled={busy} aria-label="關閉彙總列印"><X size={20} /></button></Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
    <details open={historyOpen} onToggle={(event) => { const next = event.currentTarget.open; setHistoryOpen(next); if (next) void loadHistory(); }}>
      <summary>最近列印紀錄</summary>
      {historyError && <p className="form-error" role="alert">{historyError}</p>}
      {!historyError && !history.length && <p>尚無彙總列印紀錄</p>}
      {history.map((entry) => <div key={entry.request_id}><BatchResult batch={entry} /><button type="button" className="button button-quiet" disabled={busy || Boolean(attempt && !batch && attempt.request_id !== entry.request_id)} onClick={() => {
        saveAttempt({ request_id: entry.request_id, fingerprint: entry.fingerprint, filters: entry.filters });
        setBatch(entry); setPreview(null); setReviewedPaper(false); setResolvePage(""); setResolveReason(""); setError(""); setOpen(true);
      }}>查看／核對這批</button></div>)}
      <button type="button" className="button button-quiet" onClick={() => void loadHistory()}>更新列印紀錄</button>
    </details>
  </section>;
}
