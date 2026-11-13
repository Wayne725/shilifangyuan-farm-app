import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { invoicePreferencePayload, type InvoicePreference } from "../lib/commerce";
import { invoiceStatusLabel } from "../lib/labels";
import { isTaiwanTaxId } from "./InvoicePreferenceFields";
import { RecentManualInvoiceRecords } from "./AdminManualInvoiceRecords";
import "./AdminWalkInSale.css";

const requestKey = "slf_admin_walk_in_request_v1";
type ProductCategory = "meal" | "regular";
type TaxType = "taxable" | "tax_exempt";
interface MemberPhoneMatch {
  id: string;
  name: string;
  member_number: string;
  membership_type: "member" | "trainee";
  personal_tax_type: TaxType;
  company_tax_type: TaxType;
}
interface MemberPhoneLookup {
  status: "matched" | "inactive" | "no_match";
  phone_masked: string;
  matches: MemberPhoneMatch[];
}
interface WalkInSale {
  order_id: string;
  order_number: string;
  amount_total: number;
  invoice_status?: string;
  invoice_number?: string | null;
  invoice_handling: string;
  invoice_issue_after: string | null;
  sale_category: "meal" | "product" | null;
}

function walkInInvoiceStatus(sale: WalkInSale): string {
  if (sale.invoice_handling === "pickup_day" && (!sale.invoice_status || sale.invoice_status === "pending")) {
    const date = sale.invoice_issue_after
      ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", dateStyle: "medium" }).format(new Date(sale.invoice_issue_after))
      : "待查核";
    return `預定開票日：${date}`;
  }
  if (sale.invoice_handling === "external") return invoiceStatusLabel("external");
  if (!sale.invoice_status || sale.invoice_status === "pending") return "已安排系統開票";
  const label = invoiceStatusLabel(sale.invoice_status);
  return sale.invoice_number ? `${label} · ${sale.invoice_number}` : label;
}

function defaultWalkInPreference(): InvoicePreference {
  return { buyerType: "personal", buyerEmail: "", carrierType: "mobile_barcode", carrierValue: "" };
}

function newWalkInRequestId() {
  const id = crypto.randomUUID();
  sessionStorage.setItem(requestKey, id);
  return id;
}

function walkInPreferenceIsValid(value: InvoicePreference): boolean {
  const email = value.buyerEmail.trim();
  const mobile = value.carrierType === "mobile_barcode";
  const emailIsValid = (!email && mobile) || (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && email.length <= (mobile ? 80 : 64));
  return emailIsValid && (!mobile || /^\/[0-9A-Z+\-.]{7}$/.test((value.carrierValue || "").trim().toUpperCase()))
    && (value.buyerType === "personal" || (isTaiwanTaxId(value.buyerTaxId.trim()) && value.buyerName.trim().length <= 60));
}

function WalkInInvoiceFields({ value, onChange }: { value: InvoicePreference; onChange: (value: InvoicePreference) => void }) {
  const mobile = value.carrierType === "mobile_barcode";
  const [touched, setTouched] = useState({ barcode: false, taxId: false, email: false });
  const barcodeError = touched.barcode && Boolean(value.carrierValue?.trim()) && !/^\/[0-9A-Z+\-.]{7}$/.test((value.carrierValue || "").trim().toUpperCase());
  const taxIdError = touched.taxId && value.buyerType === "company" && Boolean(value.buyerTaxId.trim()) && !isTaiwanTaxId(value.buyerTaxId.trim());
  const emailError = touched.email && Boolean(value.buyerEmail.trim()) && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.buyerEmail.trim());
  const changeBuyer = (buyerType: "personal" | "company") => onChange(buyerType === "company"
    ? { ...value, buyerType, buyerTaxId: "", buyerName: "" }
    : { buyerType, buyerEmail: value.buyerEmail, carrierType: value.carrierType, carrierValue: value.carrierValue });
  return <div className="invoice-preference">
    <div className="invoice-type-switch" role="radiogroup" aria-label="發票身分">
      <button type="button" role="radio" aria-checked={value.buyerType === "personal"} className={value.buyerType === "personal" ? "selected" : ""} onClick={() => changeBuyer("personal")}>個人電子發票</button>
      <button type="button" role="radio" aria-checked={value.buyerType === "company"} className={value.buyerType === "company" ? "selected" : ""} onClick={() => changeBuyer("company")}>公司統編發票</button>
    </div>
    <div className="invoice-preference-panel">
      {value.buyerType === "company" && <div className="invoice-company-form">
        <label>統一編號<input aria-label="統一編號" required inputMode="numeric" maxLength={8} placeholder="8 碼統一編號" aria-invalid={taxIdError || undefined} aria-describedby={taxIdError ? "walk-in-tax-id-error" : undefined} onBlur={() => setTouched(current => ({ ...current, taxId: true }))} value={value.buyerTaxId} onChange={event => onChange({ ...value, buyerTaxId: event.target.value.replace(/\D/g, "") })}/>{taxIdError && <span className="invoice-field-error" id="walk-in-tax-id-error">請輸入有效的 8 碼統一編號</span>}</label>
        <label>公司名稱<input maxLength={60} placeholder="發票抬頭" value={value.buyerName} onChange={event => onChange({ ...value, buyerName: event.target.value })}/></label>
      </div>}
      <div className="invoice-carrier-options">
        <label className={mobile ? "selected" : ""}><input type="radio" name="walk-in-carrier" checked={mobile} onChange={() => onChange({ ...value, carrierType: "mobile_barcode" })}/><strong>手機條碼載具</strong></label>
        <label className={!mobile ? "selected" : ""}><input type="radio" name="walk-in-carrier" checked={!mobile} onChange={() => onChange({ ...value, carrierType: "cloud" })}/><strong>Email 會員載具</strong></label>
        {mobile && <label className="invoice-barcode-field">手機條碼<input aria-label="手機條碼" required maxLength={8} autoCapitalize="characters" placeholder="/AB12+-." aria-invalid={barcodeError || undefined} aria-describedby={barcodeError ? "walk-in-barcode-error" : undefined} onBlur={() => setTouched(current => ({ ...current, barcode: true }))} value={value.carrierValue || ""} onChange={event => onChange({ ...value, carrierValue: event.target.value.trim().toUpperCase() })}/>{barcodeError && <span className="invoice-field-error" id="walk-in-barcode-error">請輸入以 / 開頭的 8 碼手機條碼</span>}</label>}
      </div>
      <div className="invoice-delivery-form"><label className="invoice-email-field">發票通知 Email<input aria-label="發票通知 Email" type="email" inputMode="email" autoComplete="off" required={!mobile} maxLength={mobile ? 80 : 64} placeholder="name@example.com" aria-invalid={emailError || undefined} aria-describedby={emailError ? "walk-in-email-error" : undefined} onBlur={() => setTouched(current => ({ ...current, email: true }))} value={value.buyerEmail} onChange={event => onChange({ ...value, buyerEmail: event.target.value })}/></label>{emailError && <p className="invoice-field-error" id="walk-in-email-error">請輸入正確的 Email 格式</p>}</div>
    </div>
  </div>;
}

export function AdminWalkInSale() {
  const queryClient = useQueryClient();
  const [requestId, setRequestId] = useState<string | null>(null);
  const [storageError, setStorageError] = useState("");
  const [productCategory, setProductCategory] = useState<ProductCategory>("meal");
  const [productName, setProductName] = useState("");
  const [unitPrice, setUnitPrice] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [taxType, setTaxType] = useState<TaxType>("taxable");
  const [preference, setPreference] = useState<InvoicePreference>(defaultWalkInPreference);
  const [memberMatchId, setMemberMatchId] = useState("");
  const [memberPhone, setMemberPhone] = useState("");
  const [payload, setPayload] = useState<string | null>(null);
  const [result, setResult] = useState<WalkInSale | null>(null);
  const activeRequestId = useRef<string | null>(null);
  const pendingPhone = useRef<string | null>(null);
  const pendingSubmission = useRef<{ requestId: string; body: string } | null>(null);
  const submitting = useRef<string | null>(null);
  const selectRequest = (id: string) => {
    activeRequestId.current = id;
    setRequestId(id);
  };
  useEffect(() => {
    try {
      const id = sessionStorage.getItem(requestKey) || crypto.randomUUID();
      sessionStorage.setItem(requestKey, id);
      selectRequest(id);
    } catch { setStorageError("無法保存交易識別碼，請允許瀏覽器儲存後重試。"); }
    return () => { activeRequestId.current = null; };
  }, []);
  const saleCategory = productCategory === "meal" ? "meal" : "product";
  const verification = useMutation({
    mutationFn: () => {
      const phone = pendingPhone.current;
      pendingPhone.current = null;
      if (!phone) throw new Error("請輸入電話號碼");
      return apiFetch<MemberPhoneLookup>("/v1/admin/walk-in-sales/member-phone", {
        method: "POST",
        body: JSON.stringify({ phone }),
      });
    },
    gcTime: 0,
  });
  const matches = verification.data?.matches ?? [];
  const selectedMember = matches.length === 1 ? matches[0] : matches.find(match => match.id === memberMatchId);
  const shownTax = selectedMember
    ? (preference.buyerType === "company" ? selectedMember.company_tax_type : selectedMember.personal_tax_type)
    : taxType;
  const phone = memberPhone.trim();
  const resetVerification = () => {
    pendingPhone.current = null;
    setMemberMatchId("");
    verification.reset();
  };
  const checkPhone = () => {
    if (!phone || verification.isPending) return;
    pendingPhone.current = phone;
    verification.mutate();
  };
  const recoveryKey = ["admin-walk-in-request", requestId] as const;
  const recovery = useQuery({
    queryKey: recoveryKey,
    enabled: Boolean(requestId) && !result,
    retry: false,
    refetchOnWindowFocus: () => !submitting.current,
    refetchOnReconnect: () => !submitting.current,
    queryFn: async ({ queryKey: [, id], signal }) => {
      try { return await apiFetch<WalkInSale>(`/v1/admin/walk-in-sales/request/${id}`, { signal }); }
      catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
    },
  });
  const sale = result || recovery.data;
  const invoiceRead = useQuery({
    queryKey: ["admin-walk-in-invoice-status", requestId, sale?.order_id] as const,
    enabled: false,
    retry: false,
    queryFn: ({ queryKey: [, , orderId], signal }) =>
      apiFetch<WalkInSale>(`/v1/admin/walk-in-sales/${orderId}`, { signal }),
  });
  const creation = useMutation({
    mutationFn: (id: string) => {
      const submission = pendingSubmission.current;
      if (submission?.requestId !== id || activeRequestId.current !== id) throw new Error("請重新確認交易。");
      pendingSubmission.current = null;
      return apiFetch<WalkInSale>("/v1/admin/walk-in-sales", { method: "POST", body: submission.body });
    },
    gcTime: 0,
    onSuccess: async (data, id) => {
      if (activeRequestId.current !== id) return;
      setResult(data);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin-orders"] }),
        queryClient.invalidateQueries({ queryKey: ["admin-manual-invoice-records"] }),
        queryClient.invalidateQueries({ queryKey: ["invoice-register"] }),
      ]);
    },
    onError: (error, id) => {
      if (activeRequestId.current !== id || !(error instanceof ApiError)) return;
      if ([400, 422].includes(error.status)) setPayload(null);
      if (error.status === 409 && error.code === "walk_in_member_quote_stale") {
        try { selectRequest(newWalkInRequestId()); }
        catch { setStorageError("無法更新交易識別碼，請重新整理後查核交易。"); return; }
        setPayload(null);
        resetVerification();
      }
    },
    onSettled: (_data, _error, id) => {
      if (submitting.current === id) submitting.current = null;
    },
  });
  useEffect(() => {
    if (!sale || creation.isPending) return;
    setPayload(null);
    setMemberMatchId("");
    setMemberPhone("");
    pendingPhone.current = null;
    pendingSubmission.current = null;
    verification.reset();
    creation.reset();
  }, [sale, creation.isPending, verification.reset, creation.reset]);
  const next = () => {
    if (submitting.current) return;
    let nextId: string;
    try { nextId = newWalkInRequestId(); }
    catch { setStorageError("無法更新交易識別碼，請重新整理後查核交易。"); return; }
    selectRequest(nextId); setStorageError(""); setResult(null); setPayload(null);
    setProductName("");
    setUnitPrice("");
    setQuantity("1");
    setTaxType("taxable");
    setMemberPhone("");
    resetVerification();
    setPreference(defaultWalkInPreference());
    creation.reset();
  };
  const price = Number(unitPrice), count = Number(quantity);
  const phoneVerified = !phone || (verification.isSuccess && (!matches.length || Boolean(selectedMember)));
  const valid = productName.trim() && Number.isSafeInteger(price) && price > 0 && price <= 1_000_000
    && Number.isSafeInteger(count) && count > 0 && count <= 999 && phoneVerified && walkInPreferenceIsValid(preference);
  const submit = () => {
    if (!requestId || activeRequestId.current !== requestId || submitting.current
      || queryClient.isFetching({ queryKey: recoveryKey, exact: true })
      || queryClient.getQueryData<WalkInSale | null>(recoveryKey)) return;
    const body = payload || JSON.stringify({
      client_request_id: requestId, product_name: productName.trim(), sale_category: saleCategory, unit_price: price, quantity: count,
      tax_type: shownTax, contact_email: preference.buyerEmail.trim() || null,
      ...(phone ? { phone, phone_match_id: selectedMember?.id ?? null } : {}),
      invoice_handling: "on_payment",
      preferences: { ...invoicePreferencePayload(preference), invoice_buyer_email: preference.buyerEmail.trim().toLowerCase() || null },
      reason: selectedMember ? "社員現場收款" : "現場客人收款", confirm_received: true,
      confirm_not_invoiced_elsewhere: true,
    });
    pendingSubmission.current = { requestId, body };
    submitting.current = requestId;
    setPayload(body);
    creation.mutate(requestId);
  };
  const checkRecovery = () => {
    if (!submitting.current && !queryClient.isFetching({ queryKey: recoveryKey, exact: true })) void recovery.refetch();
  };
  return <section className="admin-walk-in-sale" aria-label="現場收款與開票">
      {storageError && <p className="form-error" role="alert">{storageError}</p>}
      {recovery.isPending && requestId && <p role="status">正在查核交易紀錄…</p>}
      {recovery.isError && <div role="alert"><p>{recovery.error.message}</p><button type="button" disabled={creation.isPending || recovery.isFetching} onClick={checkRecovery}>重新查核交易</button></div>}
      {sale ? <div className="admin-walk-in-result">
          <div role="status">
            <p>已記錄收款 · {sale.order_number} · {formatMoney(sale.amount_total)}</p>
            <p>{walkInInvoiceStatus(invoiceRead.data ?? sale)}</p>
          </div>
          {invoiceRead.isError && <p className="form-error" role="alert">{invoiceRead.error.message}</p>}
          <div className="form-actions">
            <button type="button" className="button button-quiet" disabled={invoiceRead.isFetching} onClick={() => void invoiceRead.refetch()}>
              {invoiceRead.isFetching ? "更新中…" : "更新發票狀態"}
            </button>
            <Link className="button button-quiet" to="/admin" search={{ section: "orders", q: sale.order_number, order_id: sale.order_id }}>查看交易與發票狀態</Link>
            <button type="button" className="button button-system" disabled={creation.isPending} onClick={next}>下一筆現場交易</button>
          </div>
        </div>
        : requestId && recovery.isSuccess && !storageError && <form onSubmit={event => { event.preventDefault(); if (payload || valid) submit(); }}>
          <fieldset disabled={Boolean(payload) || creation.isPending}>
            <legend>交易內容</legend>
            <div className="admin-walk-in-categories" role="group" aria-label="商品分類">
              <button type="button" aria-pressed={productCategory === "meal"} onClick={() => setProductCategory("meal")}>便當</button>
              <button type="button" aria-pressed={productCategory === "regular"} onClick={() => setProductCategory("regular")}>一般商品</button>
            </div>
            <div className="field-grid two-columns">
              <label className="field"><span>品名</span><input required maxLength={120} value={productName} onChange={event => setProductName(event.target.value)} /></label>
              <label className="field"><span>含稅單價</span><input required inputMode="numeric" type="number" min={1} max={1_000_000} step={1} value={unitPrice} onChange={event => setUnitPrice(event.target.value)} /></label>
              <label className="field"><span>數量</span><input required type="number" min={1} max={999} step={1} value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
              <label className="field"><span>稅別</span><select aria-label="稅別" disabled={Boolean(selectedMember)} value={shownTax} onChange={event => setTaxType(event.target.value === "tax_exempt" ? "tax_exempt" : "taxable")}><option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label>
            </div>
            <div className="admin-walk-in-member">
              <div className="admin-walk-in-phone">
                <label className="field"><span>電話號碼</span><input type="tel" autoComplete="off" maxLength={40} readOnly={verification.isPending || verification.isSuccess} value={verification.data?.phone_masked ?? memberPhone} onChange={event => { setMemberPhone(event.target.value); resetVerification(); }} /></label>
                {verification.isSuccess
                  ? <button type="button" className="button button-quiet" onClick={() => { setMemberPhone(""); resetVerification(); }}>更改電話</button>
                  : <button type="button" className="button button-quiet" disabled={!phone || verification.isPending} onClick={checkPhone}>{verification.isPending ? "核對中…" : "核對社員"}</button>}
              </div>
              {matches.length > 1 && <div className="field"><label htmlFor="walk-in-member-match">核對社員身分</label><select id="walk-in-member-match" value={memberMatchId} onChange={event => setMemberMatchId(event.target.value)}>
                <option value="">請選擇社員</option>
                {matches.map(match => <option key={match.id} value={match.id}>{match.name}（{match.member_number}）</option>)}
              </select></div>}
              {selectedMember && <p role="status">{selectedMember.membership_type === "trainee" ? "實習社員" : "社員"}：{selectedMember.name}（{selectedMember.member_number}）</p>}
              {verification.data?.status === "no_match" && <p role="status">非社員，以一般顧客開立</p>}
              {verification.data?.status === "inactive" && <p role="status">此電話的社員資格已停止，以一般顧客開立</p>}
              {verification.isError && <p className="form-error" role="alert">{verification.error.message}</p>}
            </div>
            <WalkInInvoiceFields value={preference} onChange={setPreference} />
          </fieldset>
          {creation.isError && <p className="form-error" role="alert">{creation.error.message}</p>}
          <div className="admin-walk-in-confirm"><p>實收金額 <strong>{formatMoney(Number.isSafeInteger(price * count) ? price * count : 0)}</strong></p><div className="form-actions"><button type="submit" className="button button-system" disabled={creation.isPending || recovery.isFetching || (!payload && !valid)}>{creation.isPending ? "記錄中…" : payload ? "重試同一筆交易" : "確認"}</button>{payload && <button type="button" className="button button-quiet" disabled={creation.isPending || recovery.isFetching} onClick={checkRecovery}>{recovery.isFetching ? "查核中…" : "查核交易結果"}</button>}</div></div>
        </form>}
      <RecentManualInvoiceRecords />
  </section>;
}
