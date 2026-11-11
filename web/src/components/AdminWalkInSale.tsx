import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { invoicePreferencePayload, type InvoicePreference } from "../lib/commerce";
import { isTaiwanTaxId } from "./InvoicePreferenceFields";
import { walkInCatalogItems, type WalkInCatalogItem, type WalkInCatalogSource } from "../lib/walkInCatalog";
import { RecentManualInvoiceRecords } from "./AdminManualInvoiceRecords";
import "./AdminWalkInSale.css";

const requestKey = "slf_admin_walk_in_request_v1";
type ProductCategory = "meal" | "regular";
type BuyerKind = "guest" | "member";
type TaxType = "taxable" | "tax_exempt";
interface MemberQuoteRequest {
  member: { member_number: string; phone: string };
  sale_category: "meal" | "product";
  catalog_id: string;
  invoice_buyer_type: InvoicePreference["buyerType"];
}
interface MemberQuote {
  product_name: string;
  unit_price: number;
  tax_type: TaxType;
  member_tax_exempt: boolean;
  membership_type: "member" | "trainee";
}
interface MemberQuoteKey {
  catalogId: string;
  catalogVersion: string;
}
interface WalkInSale {
  order_id: string;
  order_number: string;
  amount_total: number;
  invoice_handling: string;
  invoice_issue_after: string | null;
  sale_category: "meal" | "product" | null;
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
    && (value.buyerType === "personal" || (isTaiwanTaxId(value.buyerTaxId.trim()) && Boolean(value.buyerName.trim()) && value.buyerName.trim().length <= 60));
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
        <label>公司名稱<input required maxLength={60} placeholder="發票抬頭" value={value.buyerName} onChange={event => onChange({ ...value, buyerName: event.target.value })}/></label>
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
  const [item, setItem] = useState<WalkInCatalogItem | null>(null);
  const [unitPrice, setUnitPrice] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [taxType, setTaxType] = useState<TaxType>("taxable");
  const [customerName, setCustomerName] = useState("");
  const [preference, setPreference] = useState<InvoicePreference>(defaultWalkInPreference);
  const [buyerKind, setBuyerKind] = useState<BuyerKind>("guest");
  const [memberNumber, setMemberNumber] = useState("");
  const [memberPhone, setMemberPhone] = useState("");
  const [payload, setPayload] = useState<string | null>(null);
  const [result, setResult] = useState<WalkInSale | null>(null);
  const activeRequestId = useRef<string | null>(null);
  const pendingQuote = useRef<{ key: MemberQuoteKey; body: MemberQuoteRequest } | null>(null);
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
  const productNames = useQuery({
    queryKey: ["admin-walk-in-product-names"],
    queryFn: async ({ signal }) => {
      const lists = await Promise.all([
        apiFetch<WalkInCatalogSource[]>("/v1/products", { signal }),
        apiFetch<WalkInCatalogSource[]>("/v1/admin/meals", { signal }),
      ]);
      return { regular: walkInCatalogItems(lists[0], "regular"), meal: walkInCatalogItems(lists[1], "meal") };
    },
    retry: false,
  });
  const availableItems = productNames.data?.[productCategory] ?? [];
  const catalogItem = item && availableItems.find(current => current.id === item.id && current.name === item.name);
  const catalogVersion = catalogItem ? JSON.stringify([catalogItem.name, catalogItem.unitPrice, catalogItem.memberPrice, catalogItem.taxType]) : "";
  const memberSale = buyerKind === "member";
  const saleCategory = productCategory === "meal" ? "meal" : "product";
  const verification = useMutation({
    mutationFn: (key: MemberQuoteKey) => {
      const request = pendingQuote.current;
      if (request?.key !== key) throw new Error("請重新核對社員與售價。");
      pendingQuote.current = null;
      return apiFetch<MemberQuote>("/v1/admin/walk-in-sales/member-quote", { method: "POST", body: JSON.stringify(request.body) });
    },
    onSuccess: (data, key) => {
      const quotedItem = availableItems.find(current => current.id === key.catalogId);
      if (quotedItem?.name.trim() !== data.product_name) void productNames.refetch();
    },
    gcTime: 0,
  });
  const memberQuote = memberSale && (payload || (catalogItem && catalogItem.name.trim() === verification.data?.product_name
    && catalogItem.id === verification.variables?.catalogId && catalogVersion === verification.variables.catalogVersion))
    ? verification.data : undefined;
  const resetVerification = () => {
    pendingQuote.current = null;
    verification.reset();
  };
  const memberIdentity = { member_number: memberNumber.trim(), phone: memberPhone.trim() };
  const changeProduct = (selected: WalkInCatalogItem | null) => {
    setItem(selected);
    setUnitPrice(selected?.unitPrice ?? "");
    setTaxType(selected?.taxType ?? "taxable");
    resetVerification();
  };
  const changeCategory = (category: ProductCategory) => {
    if (category === productCategory) return;
    setProductCategory(category);
    changeProduct(null);
    setQuantity("1");
  };
  const changeBuyerKind = (kind: BuyerKind) => {
    if (kind === buyerKind) return;
    setBuyerKind(kind);
    setMemberNumber("");
    setMemberPhone("");
    resetVerification();
  };
  const changePreference = (value: InvoicePreference) => {
    if (value.buyerType !== preference.buyerType) resetVerification();
    setPreference(value);
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
        void productNames.refetch();
      }
    },
    onSettled: (_data, _error, id) => {
      if (submitting.current === id) submitting.current = null;
    },
  });
  useEffect(() => {
    if (!sale || creation.isPending) return;
    setPayload(null);
    setMemberNumber("");
    setMemberPhone("");
    pendingQuote.current = null;
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
    changeProduct(catalogItem || (item && { ...item, unitPrice: "", taxType: undefined }));
    setQuantity("1"); setCustomerName("");
    setBuyerKind("guest");
    setMemberNumber(""); setMemberPhone("");
    setPreference(defaultWalkInPreference());
    creation.reset();
  };
  const productName = memberSale ? memberQuote?.product_name : item?.name;
  const shownPrice = memberSale ? memberQuote?.unit_price ?? "" : unitPrice;
  const shownTax = memberSale ? memberQuote?.tax_type ?? "" : taxType;
  const price = Number(shownPrice), count = Number(quantity);
  const valid = productName?.trim() && Number.isSafeInteger(price) && price > 0 && price <= 1_000_000
    && Number.isSafeInteger(count) && count > 0 && count <= 999 && walkInPreferenceIsValid(preference);
  const submit = () => {
    if (!requestId || activeRequestId.current !== requestId || submitting.current
      || queryClient.isFetching({ queryKey: recoveryKey, exact: true })
      || queryClient.getQueryData<WalkInSale | null>(recoveryKey)) return;
    const body = payload || JSON.stringify({
      client_request_id: requestId, product_name: productName?.trim(), sale_category: saleCategory, unit_price: price, quantity: count,
      tax_type: shownTax, customer_name: customerName.trim() || null, contact_email: preference.buyerEmail.trim() || null,
      ...(memberSale && catalogItem ? { member: memberIdentity, catalog_id: catalogItem.id } : {}),
      invoice_handling: "on_payment",
      preferences: { ...invoicePreferencePayload(preference), invoice_buyer_email: preference.buyerEmail.trim().toLowerCase() || null },
      reason: memberSale ? "社員現場收款" : "現場客人收款", confirm_received: true,
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
      {sale ? <div className="admin-walk-in-result" role="status"><p>已記錄收款 · {sale.order_number} · {formatMoney(sale.amount_total)}</p><p>{sale.invoice_handling === "pickup_day" ? `預定開票日：${sale.invoice_issue_after ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", dateStyle: "medium" }).format(new Date(sale.invoice_issue_after)) : "待查核"}` : "已安排系統開票"}</p><div className="form-actions"><a className="button button-quiet" href={`/admin?section=orders&q=${encodeURIComponent(sale.order_number)}`}>查看交易與發票狀態</a><button type="button" className="button button-system" disabled={creation.isPending} onClick={next}>下一筆現場交易</button></div></div>
        : requestId && recovery.isSuccess && !storageError && <form onSubmit={event => { event.preventDefault(); if (payload || valid) submit(); }}>
          <fieldset disabled={Boolean(payload) || creation.isPending}>
            <legend>交易內容</legend>
            <div className="admin-walk-in-categories" role="group" aria-label="顧客身分">
              <button type="button" aria-pressed={!memberSale} onClick={() => changeBuyerKind("guest")}>現場客人</button>
              <button type="button" aria-pressed={memberSale} onClick={() => changeBuyerKind("member")}>社員</button>
            </div>
            <div className="admin-walk-in-categories" role="group" aria-label="商品分類">
              <button type="button" aria-pressed={productCategory === "meal"} onClick={() => changeCategory("meal")}>便當</button>
              <button type="button" aria-pressed={productCategory === "regular"} onClick={() => changeCategory("regular")}>一般商品</button>
            </div>
            <div className="field-grid two-columns">
              <label className="field"><span>品名</span><select required value={catalogItem?.id || (item ? "retained" : "")} onChange={event => changeProduct(availableItems.find(current => current.id === event.target.value) ?? null)}><option value="" disabled>{productNames.isPending ? "正在載入品項…" : availableItems.length ? `請選擇${productCategory === "meal" ? "便當" : "一般商品"}` : productNames.isError ? "品項載入失敗" : "此分類沒有啟用品項"}</option>{availableItems.map(current => <option key={current.id} value={current.id} disabled={current.name.length > 120}>{current.label}</option>)}{item && !catalogItem && <option value="retained" disabled={memberSale}>{item.label}（原選品名）</option>}</select></label>
              <label className="field"><span>含稅單價</span><input required={!memberSale} readOnly={memberSale} inputMode="numeric" type="number" min={1} max={1_000_000} step={1} value={shownPrice} onChange={event => setUnitPrice(event.target.value)} /></label>
              <label className="field"><span>數量</span><input required type="number" min={1} max={999} step={1} value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
              <label className="field"><span>稅別</span><select aria-label="稅別" disabled={memberSale} value={shownTax} onChange={event => setTaxType(event.target.value === "tax_exempt" ? "tax_exempt" : "taxable")}>{memberSale && <option value="">待核對</option>}<option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label>
              <label className="field"><span>顧客姓名</span><input maxLength={80} value={customerName} onChange={event => setCustomerName(event.target.value)} /></label>
            </div>
            {productNames.isError && <div role="alert"><span>品名清單無法讀取</span><button className="text-link" type="button" onClick={() => void productNames.refetch()}>重試載入品名</button></div>}
            {memberSale && <div className="admin-walk-in-member">
              <div className="field-grid two-columns">
                <label className="field"><span>社員編號</span><input maxLength={32} autoComplete="off" value={memberNumber} onChange={event => { setMemberNumber(event.target.value); resetVerification(); }} /></label>
                <label className="field"><span>登記手機</span><input type="tel" autoComplete="off" maxLength={32} value={memberPhone} onChange={event => { setMemberPhone(event.target.value); resetVerification(); }} /></label>
              </div>
              <button type="button" className="button button-quiet" disabled={!catalogItem || !memberIdentity.member_number || !memberIdentity.phone || verification.isPending} onClick={() => {
                if (!catalogItem) return;
                creation.reset();
                const key = { catalogId: catalogItem.id, catalogVersion };
                pendingQuote.current = { key, body: {
                  member: memberIdentity, sale_category: saleCategory, catalog_id: catalogItem.id, invoice_buyer_type: preference.buyerType,
                } };
                verification.mutate(key);
              }}>{verification.isPending ? "核對中…" : "核對社員與售價"}</button>
              {memberQuote && <p role="status">已核對{memberQuote.membership_type === "trainee" ? "實習社員" : "社員"}</p>}
              {verification.data && !memberQuote && <p className="form-error" role="alert">{catalogItem ? "品項已更新，請重新核對售價。" : "品項已更新，請重新選擇後核對。"}</p>}
              {verification.isError && <p className="form-error" role="alert">{verification.error.message}</p>}
            </div>}
            <WalkInInvoiceFields value={preference} onChange={changePreference} />
          </fieldset>
          {creation.isError && <p className="form-error" role="alert">{creation.error.message}</p>}
          <div className="admin-walk-in-confirm"><p>實收金額 <strong>{memberSale && !memberQuote && !payload ? "待核對" : formatMoney(Number.isSafeInteger(price * count) ? price * count : 0)}</strong></p><div className="form-actions"><button type="submit" className="button button-system" disabled={creation.isPending || recovery.isFetching || (!payload && !valid)}>{creation.isPending ? "記錄中…" : payload ? "重試同一筆交易" : "確認"}</button>{payload && <button type="button" className="button button-quiet" disabled={creation.isPending || recovery.isFetching} onClick={checkRecovery}>{recovery.isFetching ? "查核中…" : "查核交易結果"}</button>}</div></div>
        </form>}
      <RecentManualInvoiceRecords />
  </section>;
}
