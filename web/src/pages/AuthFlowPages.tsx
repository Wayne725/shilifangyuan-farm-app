import {
  ArrowRight,
  CheckCircle,
  EnvelopeSimple,
  HandHeart,
  Key,
  LockKey,
  ShoppingBag,
  UserPlus,
} from "@phosphor-icons/react";
import { Link, useSearch } from "@tanstack/react-router";
import { type FormEvent, type ReactNode, useState } from "react";

import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import type { AuthActionResponse } from "../lib/types";

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function AuthLayout({
  eyebrow,
  title,
  description,
  icon,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="auth-page">
      <aside className="auth-page-intro">
        <Link className="auth-back-link" to="/">
          十里方圓合作社
        </Link>
        <div>
          <span className="auth-intro-icon">{icon}</span>
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        <small>既有社員需核對合作社名冊；非社員註冊不會自動取得會籍</small>
      </aside>
      <div className="auth-page-body">{children}</div>
    </section>
  );
}

function AuthResult({
  title,
  message,
  token,
  children,
}: {
  title: string;
  message: string;
  token?: string;
  children: ReactNode;
}) {
  return (
    <div className="auth-result" aria-live="polite">
      <CheckCircle size={34} weight="light" />
      <p className="eyebrow">COMPLETE</p>
      <h2>{title}</h2>
      <p>{message}</p>
      {token && (
        <div className="development-token">
          <span>本機測試碼</span>
          <strong>{token}</strong>
        </div>
      )}
      <div className="auth-result-actions">{children}</div>
    </div>
  );
}

export function RegisterPage() {
  const search = useSearch({ from: "/register" });
  const intent = search.intent;
  const [memberNumber, setMemberNumber] = useState("");
  const [legalName, setLegalName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AuthActionResponse | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if (password !== passwordConfirmation) {
      setError("兩次輸入的密碼不一致");
      return;
    }
    setBusy(true);
    try {
      const isExistingMember = intent === "existing_member";
      const response = await apiFetch<AuthActionResponse>(isExistingMember
        ? "/v1/auth/register-existing-member"
        : "/v1/auth/register", {
        method: "POST",
        body: JSON.stringify(isExistingMember
          ? {
              member_number: memberNumber.trim(),
              legal_name: legalName.trim(),
              display_name: displayName.trim(),
              email: email.trim(),
              phone: phone.trim(),
              password,
            }
          : {
              display_name: displayName.trim(),
              email: email.trim(),
              password,
            }),
      });
      setResult(response);
    } catch (reason) {
      setError(errorMessage(reason, "無法完成註冊"));
    } finally {
      setBusy(false);
    }
  };

  if (!intent) {
    return (
      <AuthLayout
        eyebrow="CHOOSE YOUR PATH"
        title="選擇註冊方式"
        description="現實中已是社員可直接認領；其他使用者先建立非社員帳號。"
        icon={<UserPlus size={29} weight="light" />}
      >
        <div className="registration-choice">
          <div className="registration-choice-heading">
            <p className="eyebrow">ACCOUNT TYPE</p>
            <h2>你想如何參與？</h2>
            <p>社員資格以合作社既有名冊為準，不能由註冊者自行選擇。</p>
          </div>
          <div className="registration-choice-grid">
            <Link className="registration-choice-card member" to="/register" search={{ intent: "existing_member" }}>
              <HandHeart size={30} weight="light" />
              <span>MEMBERSHIP</span>
              <h3>既有社員註冊</h3>
              <p>提供社員編號與名冊登記資料，核對並完成 Email 驗證後直接啟用正式社員帳號。</p>
              <strong>認領社員帳號 <ArrowRight size={18} /></strong>
            </Link>
            <Link className="registration-choice-card customer" to="/register" search={{ intent: "nonmember" }}>
              <ShoppingBag size={30} weight="light" />
              <span>GENERAL CUSTOMER</span>
              <h3>非社員註冊</h3>
              <p>建立一般買家帳號，可購物、結帳及查詢訂單；之後仍可另外提出入社申請。</p>
              <strong>建立非社員帳號 <ArrowRight size={18} /></strong>
            </Link>
          </div>
          <p className="auth-switch">
            已經有帳號？ <LoginButton />
          </p>
        </div>
      </AuthLayout>
    );
  }

  const isExistingMember = intent === "existing_member";

  return (
    <AuthLayout
      eyebrow={isExistingMember ? "MEMBER ACCOUNT CLAIM" : "NONMEMBER REGISTRATION"}
      title={isExistingMember ? "既有社員註冊" : "非社員註冊"}
      description={isExistingMember
        ? "核對合作社名冊並驗證 Email，完成後直接取得既有正式社員資格。"
        : "完成 Email 驗證後，即可使用購物、結帳與訂單服務。"}
      icon={isExistingMember ? <HandHeart size={29} weight="light" /> : <ShoppingBag size={29} weight="light" />}
    >
      {result ? (
        <AuthResult
          title="請驗證你的 Email"
          message={result.message}
          token={result.development_token}
        >
          <Link
            className="button button-primary"
            to="/verify-email"
            search={{ email, token: result.development_token, intent }}
          >
            輸入驗證碼 <ArrowRight size={17} />
          </Link>
        </AuthResult>
      ) : (
        <div className="auth-card">
          <Link className="registration-back" to="/register" search={{ intent: undefined }}>
            重新選擇註冊方式
          </Link>
          <p className="eyebrow">ACCOUNT DETAILS</p>
          <h2>{isExistingMember ? "認領社員帳號" : "建立非社員帳號"}</h2>
          {isExistingMember && (
            <p className="registration-notice">資料必須與合作社名冊一致；若名冊 Email 或手機已更換，請先聯絡合作社更新。</p>
          )}
          <form className="auth-form" onSubmit={submit}>
            {isExistingMember && (
              <label className="field">
                社員編號
                <input
                  value={memberNumber}
                  onChange={(event) => setMemberNumber(event.target.value)}
                  autoComplete="off"
                  maxLength={32}
                  required
                />
              </label>
            )}
            {isExistingMember && (
              <label className="field">
                名冊登記姓名
                <input
                  value={legalName}
                  onChange={(event) => setLegalName(event.target.value)}
                  autoComplete="name"
                  maxLength={80}
                  required
                />
              </label>
            )}
            <label className="field">
              顯示名稱
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                autoComplete={isExistingMember ? "nickname" : "name"}
                maxLength={80}
                required
              />
            </label>
            {isExistingMember && (
              <small className="field-hint">
                顯示名稱可使用暱稱；名冊姓名只用於社員身分核對。
              </small>
            )}
            {isExistingMember && (
              <label className="field">
                名冊登記手機
                <input
                  value={phone}
                  onChange={(event) => setPhone(event.target.value)}
                  type="tel"
                  autoComplete="tel"
                  minLength={8}
                  maxLength={24}
                  required
                />
              </label>
            )}
            <label className="field">
              電子信箱
              <input
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                autoComplete="email"
                required
              />
            </label>
            <div className="field-grid two-columns">
              <label className="field">
                密碼
                <input
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={128}
                  required
                />
              </label>
              <label className="field">
                再次輸入密碼
                <input
                  value={passwordConfirmation}
                  onChange={(event) => setPasswordConfirmation(event.target.value)}
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={128}
                  required
                />
              </label>
            </div>
            <small className="field-hint">密碼至少 8 個字元。</small>
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="button button-primary" disabled={busy}>
              {busy ? "建立中…" : "建立帳號"}
            </button>
          </form>
          <p className="auth-switch">
            已經有帳號？ <LoginButton />
          </p>
        </div>
      )}
    </AuthLayout>
  );
}

export function VerifyEmailPage() {
  const search = useSearch({ from: "/verify-email" });
  const { openLogin } = useAuth();
  const [email, setEmail] = useState(search.email || "");
  const [token, setToken] = useState(search.token || "");
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [verified, setVerified] = useState(false);

  const verify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiFetch<AuthActionResponse>("/v1/auth/verify-email", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), token }),
      });
      setVerified(true);
    } catch (reason) {
      setError(errorMessage(reason, "無法完成 Email 驗證"));
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    if (!email.trim()) {
      setError("請先輸入註冊使用的電子信箱");
      return;
    }
    setResending(true);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch<AuthActionResponse>(
        "/v1/auth/resend-verification",
        {
          method: "POST",
          body: JSON.stringify({ email: email.trim() }),
        },
      );
      if (response.development_token) setToken(response.development_token);
      setNotice(response.message);
    } catch (reason) {
      setError(errorMessage(reason, "無法重新寄送驗證碼"));
    } finally {
      setResending(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="VERIFY EMAIL"
      title="驗證電子信箱"
      description="輸入信件中的六位數驗證碼。驗證碼有效時間為 10 分鐘。"
      icon={<EnvelopeSimple size={29} weight="light" />}
    >
      {verified ? (
        <AuthResult
          title="Email 驗證完成"
          message={search.intent === "existing_member"
            ? "社員帳號已啟用，登入後即可使用正式社員功能。"
            : "你的非社員帳號已可登入使用。"}
        >
          <button className="button button-primary" type="button" onClick={openLogin}>
            登入開始使用 <ArrowRight size={17} />
          </button>
        </AuthResult>
      ) : (
        <div className="auth-card">
          <p className="eyebrow">VERIFICATION CODE</p>
          <h2>輸入驗證碼</h2>
          <form className="auth-form" onSubmit={verify}>
            <label className="field">
              註冊電子信箱
              <input
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                autoComplete="email"
                required
              />
            </label>
            <label className="field">
              六位數驗證碼
              <input
                className="verification-code"
                value={token}
                onChange={(event) => setToken(event.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                minLength={6}
                maxLength={6}
                placeholder="000000"
                required
              />
            </label>
            {notice && <p className="form-success" aria-live="polite">{notice}</p>}
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="button button-primary" disabled={busy}>
              {busy ? "驗證中…" : "完成驗證"}
            </button>
          </form>
          <div className="auth-resend">
            <p>沒收到信？可重新寄送到上方註冊信箱。</p>
            <button type="button" onClick={resend} disabled={resending}>
              {resending ? "寄送中…" : "重新寄送驗證碼"}
            </button>
          </div>
        </div>
      )}
    </AuthLayout>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AuthActionResponse | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch<AuthActionResponse>(
        "/v1/auth/forgot-password",
        {
          method: "POST",
          body: JSON.stringify({ email: email.trim() }),
        },
      );
      setResult(response);
    } catch (reason) {
      setError(errorMessage(reason, "無法送出密碼重設要求"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="PASSWORD HELP"
      title="重設登入密碼"
      description="我們會將限時重設連結寄到你的帳號信箱。"
      icon={<Key size={29} weight="light" />}
    >
      {result ? (
        <AuthResult
          title="請查看你的信箱"
          message={result.message}
          token={result.development_token}
        >
          {result.development_token ? (
            <Link
              className="button button-primary"
              to="/reset-password"
              search={{ token: result.development_token }}
            >
              本機測試重設 <ArrowRight size={17} />
            </Link>
          ) : (
            <Link className="button button-quiet" to="/">
              回到首頁
            </Link>
          )}
        </AuthResult>
      ) : (
        <div className="auth-card">
          <p className="eyebrow">ACCOUNT EMAIL</p>
          <h2>找回帳號</h2>
          <form className="auth-form" onSubmit={submit}>
            <label className="field">
              電子信箱
              <input
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                autoComplete="email"
                required
              />
            </label>
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="button button-primary" disabled={busy}>
              {busy ? "送出中…" : "寄送重設連結"}
            </button>
          </form>
          <p className="auth-switch">
            想起密碼了？ <LoginButton />
          </p>
        </div>
      )}
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  const search = useSearch({ from: "/reset-password" });
  const { openLogin } = useAuth();
  const [token, setToken] = useState(search.token || "");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [complete, setComplete] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if (password !== passwordConfirmation) {
      setError("兩次輸入的密碼不一致");
      return;
    }
    setBusy(true);
    try {
      await apiFetch<AuthActionResponse>("/v1/auth/reset-password", {
        method: "POST",
        body: JSON.stringify({ token: token.trim(), password }),
      });
      setComplete(true);
    } catch (reason) {
      setError(errorMessage(reason, "無法更新密碼"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="NEW PASSWORD"
      title="設定新的密碼"
      description="重設連結有效時間為一小時，完成後請使用新密碼登入。"
      icon={<LockKey size={29} weight="light" />}
    >
      {complete ? (
        <AuthResult title="密碼已更新" message="現在可以使用新密碼登入。">
          <button className="button button-primary" type="button" onClick={openLogin}>
            登入系統 <ArrowRight size={17} />
          </button>
        </AuthResult>
      ) : (
        <div className="auth-card">
          <p className="eyebrow">SECURE ACCOUNT</p>
          <h2>更新密碼</h2>
          <form className="auth-form" onSubmit={submit}>
            {!search.token && (
              <label className="field">
                密碼重設碼
                <input
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  autoComplete="off"
                  minLength={16}
                  required
                />
              </label>
            )}
            <label className="field">
              新密碼
              <input
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                autoComplete="new-password"
                minLength={8}
                maxLength={128}
                required
              />
            </label>
            <label className="field">
              再次輸入新密碼
              <input
                value={passwordConfirmation}
                onChange={(event) => setPasswordConfirmation(event.target.value)}
                type="password"
                autoComplete="new-password"
                minLength={8}
                maxLength={128}
                required
              />
            </label>
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="button button-primary" disabled={busy || !token}>
              {busy ? "更新中…" : "更新密碼"}
            </button>
          </form>
        </div>
      )}
    </AuthLayout>
  );
}

function LoginButton() {
  const { openLogin } = useAuth();
  return (
    <button type="button" onClick={openLogin}>
      返回登入
    </button>
  );
}
