import { useSearch } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { apiFetch, saveSession } from "../lib/api";
import { LoadingLines } from "../components/Shared";
import type { AuthResponse } from "../lib/types";

type LineResult = {
  status: "authenticated" | "linked" | "needs_email" | "cancelled";
  return_to: string;
  message?: string;
  intent?: "member" | "nonmember";
  session?: AuthResponse;
};

export function LineLoginPage() {
  const search = useSearch({ from: "/auth/line/callback" });
  const { isAuthReady, sessionError, retrySession, openLogin } = useAuth();
  const started = useRef(false);
  const [result, setResult] = useState<LineResult | null>(null);
  const [error, setError] = useState("");
  const finish = (outcome: LineResult) => {
    window.history.replaceState(null, "", "/auth/line/callback");
    if (outcome.session) saveSession(outcome.session);
    if (outcome.status === "authenticated" || outcome.status === "linked") {
      window.location.replace(outcome.return_to);
      return;
    }
    setResult(outcome);
  };
  useEffect(() => {
    if (!isAuthReady || started.current) return;
    started.current = true;
    const request = search.state
      ? apiFetch<LineResult>("/v1/auth/line/callback", { method: "POST", body: JSON.stringify({ state: search.state, code: search.code || "", error: search.error || "" }) })
      : apiFetch<LineResult>("/v1/auth/line/pending");
    void request.then((outcome) => outcome.status === "needs_email"
      ? apiFetch<LineResult>("/v1/auth/line/register", { method: "POST", body: "{}" })
      : outcome).then(finish).catch((reason: unknown) => {
      window.history.replaceState(null, "", "/auth/line/callback");
      setError(reason instanceof Error ? reason.message : "LINE登入無法完成，請重新登入");
    });
  }, [isAuthReady, search.code, search.error, search.state]);
  return <section className="line-callback-page"><h1>LINE登入</h1>
    {!isAuthReady && !sessionError && <LoadingLines count={3} />}
    {sessionError && <><p className="form-error">{sessionError}</p><button className="button button-primary" type="button" onClick={retrySession}>重新確認登入狀態</button></>}
    {isAuthReady && !result && !error && <><p role="status">正在安全確認LINE授權，請稍候…</p><LoadingLines count={2} /></>}
    {result?.status === "cancelled" && <><p>{result.message}</p><a className="button button-quiet" href={result.return_to}>返回原頁</a></>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {error && <div className="line-callback-actions"><button className="button button-quiet" type="button" onClick={openLogin}>使用原Email帳號登入</button><a className="text-link" href="/shop">回到便當預訂</a></div>}
  </section>;
}
