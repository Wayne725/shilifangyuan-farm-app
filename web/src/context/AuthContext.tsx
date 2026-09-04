import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type FormEvent,
  type ReactNode,
  useContext,
  useEffect,
  useState,
} from "react";

import {
  apiFetch,
  clearSession,
  restoreSession,
  saveSession,
} from "../lib/api";
import type { AuthResponse, User } from "../lib/types";

interface AuthContextValue {
  user: User | null;
  isAuthReady: boolean;
  openLogin: () => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void restoreSession()
      .then((session) => {
        if (active) setUser(session?.user ?? null);
      })
      .finally(() => {
        if (active) setIsAuthReady(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const session = await apiFetch<AuthResponse>("/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      saveSession(session);
      queryClient.clear();
      setUser(session.user);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登入失敗");
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    try {
      await apiFetch<void>("/v1/auth/logout", { method: "POST" });
    } finally {
      clearSession();
      queryClient.clear();
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider
      value={{ user, isAuthReady, openLogin: () => setOpen(true), logout }}
    >
      {children}
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="login-dialog">
            <Dialog.Close className="icon-button dialog-close" aria-label="關閉">
              <X size={20} weight="light" />
            </Dialog.Close>
            <p className="eyebrow">ACCOUNT ACCESS</p>
            <Dialog.Title>回到合作生活</Dialog.Title>
            <Dialog.Description>
              社員與一般消費者都使用合作社帳號登入。
            </Dialog.Description>
            <form className="login-form" onSubmit={login}>
              <label>
                電子信箱
                <input name="email" type="email" autoComplete="email" required />
              </label>
              <label>
                密碼
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </label>
              {error && <p className="form-error">{error}</p>}
              <button className="button button-primary" disabled={busy}>
                {busy ? "登入中…" : "登入系統"}
              </button>
            </form>
            <div className="login-links">
              <a href="/forgot-password" onClick={() => setOpen(false)}>
                忘記密碼
              </a>
              <a href="/register" onClick={() => setOpen(false)}>
                註冊帳號
              </a>
            </div>
            <p className="dialog-footnote">
              尚未完成 Email 驗證？{" "}
              <a href="/verify-email" onClick={() => setOpen(false)}>
                輸入或重寄驗證碼
              </a>
            </p>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("AuthProvider is missing");
  return context;
}
