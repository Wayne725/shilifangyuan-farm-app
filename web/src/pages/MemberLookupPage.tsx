import { ArrowLeft } from "@phosphor-icons/react";
import { Link, useSearch } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { apiFetch } from "../lib/api";
import { lineLoginReturnTo } from "../lib/login-navigation";
import "../styles/auth-options.css";

export function MemberLookupPage() {
  const search = useSearch({ from: "/member-lookup" });
  const returnTo = lineLoginReturnTo(search.return_to || "/shop");
  const navigation = { return_to: returnTo, remember_me: search.remember_me !== false };
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const result = await apiFetch<{ message: string }>("/v1/auth/member-lookup", {
        method: "POST",
        body: JSON.stringify({ phone: phone.trim(), email: email.trim() }),
      });
      setMessage(result.message);
      setPhone("");
      setEmail("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法受理查詢，請稍後再試");
    } finally {
      setBusy(false);
    }
  };

  return <section className="line-consent-page member-lookup-page">
    <Link className="text-link" to="/line-consent" search={navigation}><ArrowLeft size={17} />返回 LINE 登入</Link>
    <p className="eyebrow">MEMBERSHIP LOOKUP / 社員查詢</p>
    <h1>查詢社員資格</h1>
    {message ? <div className="member-lookup-result">
      <p role="status">{message}</p>
      <div className="line-consent-actions">
        <Link className="button button-primary" to="/line-consent" search={navigation}>返回登入</Link>
        <Link className="text-link" to="/register" search={{ ...navigation, intent: "existing_member" }}>既有社員註冊</Link>
        <button className="text-link" type="button" onClick={() => setMessage("")}>再次查詢</button>
      </div>
    </div> : <form className="member-lookup-form" onSubmit={submit} aria-busy={busy}>
      <label className="field"><span>手機號碼</span><input required type="tel" inputMode="tel" autoComplete="tel" minLength={8} maxLength={32} value={phone} disabled={busy} onChange={(event) => { setPhone(event.target.value); setError(""); }} /></label>
      <label className="field"><span>入社時登記的 Email</span><input required type="email" autoComplete="email" maxLength={254} value={email} disabled={busy} onChange={(event) => { setEmail(event.target.value); setError(""); }} /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="button button-primary" disabled={busy || !phone.trim() || !email.trim()}>{busy ? "查詢送出中…" : "寄送查詢結果"}</button>
    </form>}
    <div className="line-consent-alternatives"><Link className="text-link" to="/help">查詢說明與常見問題</Link></div>
  </section>;
}
