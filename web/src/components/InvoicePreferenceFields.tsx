import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import type { InvoicePreference } from "../lib/commerce";
import "./InvoicePreferenceFields.css";


const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function invoiceEmailLimit(value: InvoicePreference): number {
  return value.carrierType === "cloud" ? 64 : 80;
}

function invoiceEmailIsValid(value: InvoicePreference): boolean {
  const email = value.buyerEmail.trim();
  return emailPattern.test(email) && email.length <= invoiceEmailLimit(value);
}

export function defaultInvoicePreference(email = "", barcode?: string | null): InvoicePreference {
  return {
    buyerType: "personal",
    buyerEmail: email,
    carrierType: barcode ? "mobile_barcode" : "cloud",
    saveCarrier: false,
    ...(barcode ? { carrierValue: barcode } : {}),
  };
}

export function useInvoicePreference(email: string) {
  const { user } = useAuth();
  const [ownerId, setOwnerId] = useState(user?.id);
  const [preference, setPreference] = useState(
    () => defaultInvoicePreference(email, user?.saved_mobile_barcode),
  );
  if (ownerId !== user?.id) {
    setOwnerId(user?.id);
    setPreference(defaultInvoicePreference(email, user?.saved_mobile_barcode));
  }
  useEffect(() => {
    if (email) setPreference((current) => current.buyerEmail ? current : { ...current, buyerEmail: email });
  }, [email]);
  return [preference, setPreference] as const;
}

export function isTaiwanTaxId(value: string): boolean {
  if (!/^\d{8}$/.test(value)) return false;
  const weights = [1, 2, 1, 2, 1, 2, 4, 1];
  const checksum = value
    .split("")
    .reduce((total, digit, index) => {
      const product = Number(digit) * weights[index];
      return total + Math.floor(product / 10) + (product % 10);
    }, 0);
  return checksum % 10 === 0 || (value[6] === "7" && (checksum + 1) % 10 === 0);
}

export function invoicePreferenceIsValid(value: InvoicePreference): boolean {
  const carrierIsValid =
    value.carrierType === "cloud" ||
    /^\/[0-9A-Z+\-.]{7}$/.test((value.carrierValue || "").trim().toUpperCase());
  const emailIsValid = invoiceEmailIsValid(value);
  if (value.buyerType === "company") {
    return Boolean(
      isTaiwanTaxId(value.buyerTaxId.trim()) &&
        value.buyerName.trim() &&
        emailIsValid &&
        carrierIsValid,
    );
  }
  return emailIsValid && carrierIsValid;
}


function InvoiceCarrierFields({
  value,
  onChange,
  sectionNumber,
}: {
  value: InvoicePreference;
  onChange: (value: InvoicePreference) => void;
  sectionNumber: string;
}) {
  const { user, saveMobileBarcode } = useAuth();
  const [saveState, setSaveState] = useState({ status: "", error: "" });
  const saveSequence = useRef(0);
  const removeSavedBarcode = () => {
    if (!user) return;
    onChange({ ...value, saveCarrier: false });
    const sequence = ++saveSequence.current;
    setSaveState({ status: "saving", error: "" });
    void saveMobileBarcode(null).then(() => {
      if (sequence === saveSequence.current) setSaveState({ status: "saved", error: "" });
    }).catch((reason: unknown) => {
      if (sequence === saveSequence.current) setSaveState({ status: "error", error: reason instanceof Error ? reason.message : "移除載具失敗，請重試" });
    });
  };
  const cloudTitle = value.buyerType === "personal"
    ? "Email 會員載具"
    : "Email 雲端交付";

  return (
    <div className="invoice-carrier-options">
      <label className={value.carrierType === "cloud" ? "selected" : ""}>
        <input
          checked={value.carrierType === "cloud"}
          name={`invoice-carrier-${sectionNumber}`}
          onChange={() => onChange({ ...value, carrierType: "cloud" })}
          type="radio"
        />
        <span><strong>{cloudTitle}</strong></span>
      </label>
      <label className={value.carrierType === "mobile_barcode" ? "selected" : ""}>
        <input
          checked={value.carrierType === "mobile_barcode"}
          name={`invoice-carrier-${sectionNumber}`}
          onChange={() => onChange({ ...value, carrierType: "mobile_barcode", carrierValue: value.carrierValue || user?.saved_mobile_barcode || "" })}
          type="radio"
        />
        <span><strong>手機條碼載具</strong></span>
      </label>
      {value.carrierType === "mobile_barcode" && <>
        <label className="invoice-barcode-field">
          手機條碼
          <input
            autoCapitalize="characters"
            maxLength={8}
            onChange={(event) => {
              const next = event.target.value.trim().toUpperCase();
              onChange({ ...value, carrierValue: next });
            }}
            placeholder="/AB12+-."
            value={value.carrierValue || ""}
          />
        </label>
        {user && <div className="invoice-carrier-storage">
          <label className="invoice-save-carrier">
            <input type="checkbox" checked={value.saveCarrier === true} disabled={saveState.status === "saving"} onChange={event => onChange({ ...value, saveCarrier: event.target.checked })} />
            <span>結帳時儲存此手機條碼供下次使用</span>
          </label>
          {saveState.status === "saving" && <p role="status">正在移除載具…</p>}
          {saveState.status === "saved" && <p role="status">已移除儲存載具</p>}
          {saveState.status === "error" && <div role="alert"><p className="invoice-field-error">{saveState.error}</p><button className="text-link" type="button" onClick={removeSavedBarcode}>重試移除載具</button></div>}
          {user.saved_mobile_barcode && <button className="text-link" type="button" disabled={saveState.status === "saving"} onClick={removeSavedBarcode}>移除已儲存載具</button>}
        </div>}
      </>}
    </div>
  );
}

export function InvoicePreferenceSummary({ value, typeLabel = "電子發票" }: { value: InvoicePreference; typeLabel?: string }) {
  const invoiceLabel = value.buyerType === "company"
    ? `公司統編發票${value.carrierType === "mobile_barcode" ? "＋手機載具" : ""}`
    : value.carrierType === "mobile_barcode"
      ? "個人發票＋手機載具"
      : "Email 會員載具";

  return (
    <>
      <div className="invoice-summary-line">
        <dt>{typeLabel}</dt>
        <dd>{invoiceLabel}</dd>
      </div>
      {value.buyerType === "company" && (
        <>
          <div className="invoice-summary-line">
            <dt>統一編號</dt>
            <dd>{value.buyerTaxId || "尚未填寫"}</dd>
          </div>
          <div className="invoice-summary-line">
            <dt>發票抬頭</dt>
            <dd>{value.buyerName || "尚未填寫"}</dd>
          </div>
        </>
      )}
      <div className="invoice-summary-line">
        <dt>發票 Email</dt>
        <dd>{value.buyerEmail || "尚未填寫"}</dd>
      </div>
      {value.carrierType === "mobile_barcode" && (
        <div className="invoice-summary-line">
          <dt>手機條碼</dt>
          <dd>{value.carrierValue || "尚未填寫"}</dd>
        </div>
      )}
    </>
  );
}

export function InvoicePreferenceFields({
  value,
  onChange,
  sectionNumber = "03",
  showHeading = true,
}: {
  value: InvoicePreference;
  onChange: (value: InvoicePreference) => void;
  sectionNumber?: string;
  showHeading?: boolean;
}) {
  const { user } = useAuth();
  const emailLimit = invoiceEmailLimit(value);
  const emailIsValid = invoiceEmailIsValid(value);
  const emailErrorId = `invoice-email-error-${sectionNumber}`;
  const emailError = !value.buyerEmail.trim()
    ? "請填寫發票通知 Email"
    : value.buyerEmail.trim().length > emailLimit
      ? `此發票方式的 Email 不可超過 ${emailLimit} 字元`
      : !emailPattern.test(value.buyerEmail.trim())
        ? "請輸入正確的 Email 格式"
        : "";

  return (
    <div className="invoice-preference">
      {showHeading && <div className="checkout-step-title invoice-title">
        <span>{sectionNumber}</span>
        <div><p className="eyebrow">CLOUD INVOICE / 電子發票</p><h2>電子發票</h2></div>
      </div>}
      <div className="invoice-type-switch" role="radiogroup" aria-label="發票身分">
        <button
          aria-checked={value.buyerType === "personal"}
          className={value.buyerType === "personal" ? "selected" : ""}
          onClick={() => onChange({
            buyerType: "personal",
            buyerEmail: value.buyerEmail,
            carrierType: value.carrierType,
            carrierValue: value.carrierValue,
            saveCarrier: value.saveCarrier,
          })}
          role="radio"
          type="button"
        >
          <strong>個人電子發票</strong>
        </button>
        <button
          aria-checked={value.buyerType === "company"}
          className={value.buyerType === "company" ? "selected" : ""}
          onClick={() => onChange({
            buyerType: "company",
            buyerTaxId: "",
            buyerName: "",
            buyerEmail: value.buyerEmail,
            carrierType: value.carrierType,
            carrierValue: value.carrierValue,
            saveCarrier: value.saveCarrier,
          })}
          role="radio"
          type="button"
        >
          <strong>公司統編發票</strong>
        </button>
      </div>

      <div className="invoice-preference-panel">
        {value.buyerType === "company" && (
          <div className="invoice-company-form">
            <label>
              統一編號
              <input
                inputMode="numeric"
                maxLength={8}
                onChange={(event) => onChange({ ...value, buyerTaxId: event.target.value.replace(/\D/g, "") })}
                placeholder="8 碼統一編號"
                value={value.buyerTaxId}
              />
            </label>
            <label>
              公司名稱
              <input
                onChange={(event) => onChange({ ...value, buyerName: event.target.value })}
                placeholder="發票抬頭"
                value={value.buyerName}
              />
            </label>
          </div>
        )}

        <div className="invoice-delivery-form">
          <label className="invoice-email-field">
            發票通知 Email
            <input
              aria-describedby={emailError ? emailErrorId : undefined}
              aria-invalid={!emailIsValid}
              autoComplete="email"
              inputMode="email"
              maxLength={emailLimit}
              onChange={(event) => onChange({ ...value, buyerEmail: event.target.value })}
              placeholder="name@example.com"
              required
              type="email"
              value={value.buyerEmail}
            />
          </label>
          {emailError && <p id={emailErrorId} className="invoice-field-error">{emailError}</p>}
        </div>

        <InvoiceCarrierFields
          key={user?.id || "guest"}
          onChange={onChange}
          sectionNumber={sectionNumber}
          value={value}
        />
      </div>
    </div>
  );
}
