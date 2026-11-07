import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { invoicePreferencePayload, type InvoicePreference } from "../lib/commerce";
import { defaultInvoicePreference, InvoicePreferenceFields, invoicePreferenceIsValid } from "./InvoicePreferenceFields";

const requestKey = "slf_admin_walk_in_request_v1";
interface WalkInSale {
  order_id: string;
  order_number: string;
  amount_total: number;
  invoice_handling: string;
  invoice_issue_after: string | null;
}

export function AdminWalkInSale() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [storageError, setStorageError] = useState("");
  const [productName, setProductName] = useState("餐盒");
  const [unitPrice, setUnitPrice] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [taxType, setTaxType] = useState("taxable");
  const [customerName, setCustomerName] = useState("");
  const [handling, setHandling] = useState("on_payment");
  const [pickupDate, setPickupDate] = useState("");
  const [preference, setPreference] = useState<InvoicePreference>(() => defaultInvoicePreference());
  const [received, setReceived] = useState(false);
  const [notInvoiced, setNotInvoiced] = useState(false);
  const [payload, setPayload] = useState<string | null>(null);
  const [result, setResult] = useState<WalkInSale | null>(null);
  const recovery = useQuery({
    queryKey: ["admin-walk-in-request", requestId],
    enabled: open && Boolean(requestId) && !result,
    retry: false,
    queryFn: async () => {
      try { return await apiFetch<WalkInSale>(`/v1/admin/walk-in-sales/request/${requestId}`); }
      catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
    },
  });
  const sale = result || recovery.data;
  const creation = useMutation({
    mutationFn: (body: string) => apiFetch<WalkInSale>("/v1/admin/walk-in-sales", { method: "POST", body }),
    onSuccess: async data => { setResult(data); await queryClient.invalidateQueries({ queryKey: ["admin-orders"] }); },
    onError: error => {
      if (error instanceof ApiError && [400, 422].includes(error.status)) setPayload(null);
    },
  });
  const start = () => {
    setOpen(true);
    try {
      const saved = sessionStorage.getItem(requestKey);
      const id = saved || crypto.randomUUID();
      sessionStorage.setItem(requestKey, id);
      setRequestId(id); setStorageError("");
    } catch { setStorageError("無法保存交易識別碼，請允許瀏覽器儲存後重試。"); }
  };
  const next = () => {
    try { sessionStorage.removeItem(requestKey); }
    catch { setStorageError("無法更新交易識別碼，請重新整理後查核交易。"); return; }
    setRequestId(null); setResult(null); setPayload(null); setUnitPrice(""); setQuantity("1"); setCustomerName("");
    setPreference(defaultInvoicePreference()); setReceived(false); setNotInvoiced(false); setHandling("on_payment"); setPickupDate("");
    creation.reset(); setOpen(false);
  };
  const price = Number(unitPrice), count = Number(quantity);
  const valid = productName.trim() && Number.isSafeInteger(price) && price > 0 && price <= 1_000_000
    && Number.isSafeInteger(count) && count > 0 && count <= 999 && invoicePreferenceIsValid(preference)
    && (handling !== "pickup_day" || pickupDate) && received && notInvoiced;
  const submit = () => {
    if (!requestId || creation.isPending) return;
    const body = payload || JSON.stringify({
      client_request_id: requestId, product_name: productName.trim(), unit_price: price, quantity: count,
      tax_type: taxType, customer_name: customerName.trim() || null, contact_email: preference.buyerEmail.trim(),
      invoice_handling: handling, ...(handling === "pickup_day" ? { pickup_date: pickupDate } : {}),
      preferences: invoicePreferencePayload(preference), reason: "現場客人收款", confirm_received: received,
      confirm_not_invoiced_elsewhere: notInvoiced,
    });
    setPayload(body); creation.mutate(body);
  };
  return <section className="admin-walk-in-sale" aria-label="現場收款與開票">
    {!open ? <button className="button button-system" type="button" onClick={start}>現場收款與開票</button> : <>
      <h2>現場收款與開票</h2>
      {storageError && <p className="form-error" role="alert">{storageError}</p>}
      {recovery.isPending && requestId && <p role="status">正在查核交易紀錄…</p>}
      {recovery.isError && <div role="alert"><p>{recovery.error.message}</p><button type="button" onClick={() => void recovery.refetch()}>重新查核交易</button></div>}
      {sale ? <div role="status"><p>已記錄收款 · {sale.order_number} · {formatMoney(sale.amount_total)}</p><p>{sale.invoice_handling === "pickup_day" ? `預定開票日：${sale.invoice_issue_after ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", dateStyle: "medium" }).format(new Date(sale.invoice_issue_after)) : "待查核"}` : "已安排系統開票"}</p><a className="button button-quiet" href={`/admin?section=orders&q=${encodeURIComponent(sale.order_number)}`}>查看交易與發票狀態</a><button type="button" className="button button-system" onClick={next}>下一筆現場交易</button></div>
        : requestId && recovery.isSuccess && !storageError && <form onSubmit={event => { event.preventDefault(); if (payload || valid) submit(); }}>
          <fieldset disabled={Boolean(payload) || creation.isPending}>
            <legend>交易內容</legend>
            <div className="field-grid two-columns">
              <label className="field"><span>品名</span><input required maxLength={120} value={productName} onChange={event => setProductName(event.target.value)} /></label>
              <label className="field"><span>含稅單價</span><input required inputMode="numeric" type="number" min={1} max={1_000_000} step={1} value={unitPrice} onChange={event => setUnitPrice(event.target.value)} /></label>
              <label className="field"><span>數量</span><input required type="number" min={1} max={999} step={1} value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
              <label className="field"><span>稅別</span><select aria-label="稅別" value={taxType} onChange={event => setTaxType(event.target.value)}><option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label>
              <label className="field"><span>顧客姓名（選填）</span><input maxLength={80} value={customerName} onChange={event => setCustomerName(event.target.value)} /></label>
              <label className="field"><span>開票時機</span><select aria-label="開票時機" value={handling} onChange={event => setHandling(event.target.value)}><option value="on_payment">現在開立</option><option value="pickup_day">先收款，取餐日開立</option></select></label>
              {handling === "pickup_day" && <label className="field"><span>取餐日期</span><input required type="date" value={pickupDate} onChange={event => setPickupDate(event.target.value)} /></label>}
              <label className="field"><span>發票交付</span><select aria-label="發票交付" value="electronic" onChange={() => {}}><option value="electronic">Email／手機載具</option><option value="paper" disabled>紙本證明聯（列印尚未設定）</option></select></label>
            </div>
            <InvoicePreferenceFields value={preference} onChange={setPreference} sectionNumber="walk-in" showHeading={false} allowSavedCarrier={false} />
            <p>實收金額：{formatMoney(Number.isSafeInteger(price * count) ? price * count : 0)}</p>
            <label className="check-field"><input type="checkbox" checked={received} onChange={event => setReceived(event.target.checked)} />已確認收到現金</label>
            <label className="check-field"><input type="checkbox" checked={notInvoiced} onChange={event => setNotInvoiced(event.target.checked)} />已確認此筆未由其他系統開立發票</label>
          </fieldset>
          {creation.isError && <p className="form-error" role="alert">{creation.error.message}</p>}
          <div className="form-actions"><button type="submit" className="button button-system" disabled={creation.isPending || (!payload && !valid)}>{creation.isPending ? "記錄中…" : payload ? "重試同一筆交易" : "確認收款與開票安排"}</button>{!payload && <button type="button" className="button button-quiet" onClick={() => setOpen(false)}>收起</button>}{payload && <button type="button" className="button button-quiet" disabled={creation.isPending} onClick={() => void recovery.refetch()}>查核交易結果</button>}</div>
        </form>}
    </>}
  </section>;
}
