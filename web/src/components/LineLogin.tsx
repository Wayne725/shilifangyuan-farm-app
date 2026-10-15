import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

interface LineStatus {
  ready: boolean;
  linked: boolean;
  identity_verified: boolean;
  message: string;
}

type LineLoginProps = {
  intent?: "member" | "nonmember";
  rememberMe?: boolean;
  mode?: "login" | "link";
  returnTo?: string;
};

export function LineLoginButton({ intent = "nonmember", rememberMe = true, mode = "login", returnTo }: LineLoginProps) {
  const status = useQuery({ queryKey: ["line-login-status", mode], queryFn: () => apiFetch<LineStatus>("/v1/auth/line/status"), retry: false });
  const start = useMutation({
    mutationFn: () => apiFetch<{ authorization_url: string }>("/v1/auth/line/start", { method: "POST", body: JSON.stringify({
      intent, remember_me: rememberMe, mode,
      return_to: returnTo || `${window.location.pathname}${window.location.search}`,
    }) }),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
  });
  return <div className="line-login-entry">
    <button className="button line-login-button" type="button" disabled={!status.data?.ready || start.isPending} onClick={() => start.mutate()}>
      {start.isPending ? "正在開啟LINE授權…" : mode === "link" ? "連結LINE" : "使用LINE登入"}
    </button>
    {status.isPending && <p role="status">正在確認LINE登入服務…</p>}
    {status.data && !status.data.ready && <p>{status.data.message}</p>}
    {status.isError && <p className="form-error">LINE服務暫時無法確認，可先使用Email登入。</p>}
    {start.isError && <p className="form-error" role="alert">{start.error.message}</p>}
  </div>;
}

export function LineAccountLink({ userId, emailVerified, hasEmail = true }: { userId: string; emailVerified: boolean; hasEmail?: boolean }) {
  const status = useQuery({ queryKey: ["line-account-status", userId], queryFn: () => apiFetch<LineStatus>("/v1/auth/line/status") });
  return <section className="line-account-link" aria-label="LINE帳號連結"><h2>LINE登入</h2>
    {status.data?.linked ? <><p>已連結LINE</p>{hasEmail && <p>{emailVerified ? "Email已驗證。" : "Email尚未驗證。"}</p>}</>
      : <><p>連結後沿用目前帳號的訂單與社員資格。</p><LineLoginButton mode="link" returnTo="/account" /></>}
    {status.isError && <p className="form-error">{status.error.message}</p>}
  </section>;
}
