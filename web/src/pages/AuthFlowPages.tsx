import {
  ArrowRight,
  CheckCircle,
  EnvelopeSimple,
  Key,
  LockKey,
} from "@phosphor-icons/react";
import { Link, Navigate, useSearch } from "@tanstack/react-router";
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
      <p className="eyebrow">COMPLETE / 已完成</p>
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


export function VerifyEmailPage() {
  const search = useSearch({ from: "/verify-email" });
  const { user, openLogin } = useAuth();
  const [email, setEmail] = useState(search.email || "");
  const [token, setToken] = useState(search.token || "");
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [verified, setVerified] = useState(false);

  if (user && !(search.intent === "existing_member" && search.email === user.email)) return <Navigate to="/account" replace />;

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
      eyebrow="VERIFY EMAIL / 驗證信箱"
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
          <button className="button button-primary" type="button" onClick={() => user ? window.location.replace("/account") : openLogin()}>
            登入開始使用 <ArrowRight size={17} />
          </button>
        </AuthResult>
      ) : (
        <div className="auth-card">
          <p className="eyebrow">VERIFICATION CODE / 驗證碼</p>
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
      eyebrow="PASSWORD HELP / 忘記密碼"
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
          <p className="eyebrow">ACCOUNT EMAIL / 帳號信箱</p>
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
      eyebrow="NEW PASSWORD / 重設密碼"
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
          <p className="eyebrow">SECURE ACCOUNT / 帳號安全</p>
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
