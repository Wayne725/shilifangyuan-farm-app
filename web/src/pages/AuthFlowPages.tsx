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
        <small>兩種註冊共用同一套登入，社員資格另依入社流程確認</small>
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
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
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
      const response = await apiFetch<AuthActionResponse>("/v1/auth/register", {
        method: "POST",
        body: JSON.stringify({
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
        description="先確認你要一般消費，或希望接續提出入社申請。"
        icon={<UserPlus size={29} weight="light" />}
      >
        <div className="registration-choice">
          <div className="registration-choice-heading">
            <p className="eyebrow">ACCOUNT TYPE</p>
            <h2>你想如何參與？</h2>
            <p>兩種方式都會先建立登入帳號，之後仍可申請或調整社員資格。</p>
          </div>
          <div className="registration-choice-grid">
            <Link className="registration-choice-card member" to="/register" search={{ intent: "member" }}>
              <HandHeart size={30} weight="light" />
              <span>MEMBERSHIP</span>
              <h3>社員註冊</h3>
              <p>建立帳號並接續填寫入社申請；完成審核、繳費及合作社確認後才取得社員資格。</p>
              <strong>開始社員註冊 <ArrowRight size={18} /></strong>
            </Link>
            <Link className="registration-choice-card customer" to="/register" search={{ intent: "customer" }}>
              <ShoppingBag size={30} weight="light" />
              <span>GENERAL CUSTOMER</span>
              <h3>一般消費者註冊</h3>
              <p>建立一般消費帳號，可購物、結帳、查詢訂單；不會自動取得社員資格。</p>
              <strong>開始一般註冊 <ArrowRight size={18} /></strong>
            </Link>
          </div>
          <p className="auth-switch">
            已經有帳號？ <LoginButton />
          </p>
        </div>
      </AuthLayout>
    );
  }

  const isMemberIntent = intent === "member";

  return (
    <AuthLayout
      eyebrow={isMemberIntent ? "MEMBERSHIP REGISTRATION" : "CUSTOMER REGISTRATION"}
      title={isMemberIntent ? "社員註冊" : "一般消費者註冊"}
      description={isMemberIntent
        ? "先建立並驗證帳號，登入後接續填寫入社申請。"
        : "完成 Email 驗證後，即可使用購物、結帳與訂單服務。"}
      icon={isMemberIntent ? <HandHeart size={29} weight="light" /> : <ShoppingBag size={29} weight="light" />}
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
          <h2>{isMemberIntent ? "建立入社用帳號" : "建立消費帳號"}</h2>
          {isMemberIntent && (
            <p className="registration-notice">這一步只建立登入帳號；Email 驗證完成後，還需要填寫入社資料並交由合作社確認。</p>
          )}
          <form className="auth-form" onSubmit={submit}>
            <label className="field">
              顯示名稱
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                autoComplete="name"
                maxLength={80}
                required
              />
            </label>
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
        body: JSON.stringify({ token }),
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
          message={search.intent === "member"
            ? "帳號已建立，登入後即可接續填寫入社申請。"
            : "你的一般消費帳號已可登入使用。"}
        >
          {search.intent === "member" ? (
            <Link className="button button-primary" to="/membership">
              前往入社申請 <ArrowRight size={17} />
            </Link>
          ) : (
            <button className="button button-primary" type="button" onClick={openLogin}>
              登入開始使用 <ArrowRight size={17} />
            </button>
          )}
        </AuthResult>
      ) : (
        <div className="auth-card">
          <p className="eyebrow">VERIFICATION CODE</p>
          <h2>輸入驗證碼</h2>
          <form className="auth-form" onSubmit={verify}>
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
            <label className="field">
              沒收到信？輸入註冊信箱
              <input
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                autoComplete="email"
              />
            </label>
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
