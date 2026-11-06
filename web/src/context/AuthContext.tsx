import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  apiFetch,
  clearSession,
  restoreSession,
  saveSession,
  subscribeSession,
  updateSessionUser,
} from "../lib/api";
import type { AuthResponse, User } from "../lib/types";
import { rememberLoginEmail } from "../lib/login-preferences";
import { loginReturnTo } from "../lib/login-navigation";
import "../styles/auth-options.css";

interface AuthContextValue {
  user: User | null;
  isAuthReady: boolean;
  sessionError: string;
  sessionScope: number;
  retrySession: () => void;
  openLogin: () => void;
  login: (email: string, password: string, rememberMe: boolean) => Promise<void>;
  logout: () => Promise<void>;
  saveMobileBarcode: (barcode: string | null) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [sessionAttempt, setSessionAttempt] = useState(0);
  const [sessionScope, setSessionScope] = useState(0);
  const currentUser = useRef(user);
  currentUser.current = user;
  const preferenceQueue = useRef(Promise.resolve());

  const saveMobileBarcode = useCallback((barcode: string | null) => {
    const ownerId = user?.id;
    const normalized = barcode?.trim().toUpperCase() || null;
    const saving = preferenceQueue.current.then(async () => {
      if (!ownerId || currentUser.current?.id !== ownerId) throw new Error("請先登入");
      if ((currentUser.current.saved_mobile_barcode || null) === normalized) return;
      const updated = await apiFetch<User>("/v1/auth/preferences", {
        method: "PATCH",
        headers: { "X-Expected-User-ID": ownerId },
        body: JSON.stringify({ saved_mobile_barcode: normalized }),
      });
      if (currentUser.current?.id !== ownerId || updated.id !== ownerId) {
        throw new Error("登入帳號已變更，請重新整理後再試");
      }
      updateSessionUser({ ...currentUser.current, saved_mobile_barcode: updated.saved_mobile_barcode });
    });
    preferenceQueue.current = saving.catch(() => {});
    return saving;
  }, [user?.id]);

  useEffect(() => subscribeSession((nextUser, identityChanged) => {
    if (identityChanged) {
      if (currentUser.current) {
        queryClient.clear();
        setSessionScope((value) => value + 1);
      } else {
        void queryClient.resetQueries();
      }
    }
    currentUser.current = nextUser;
    setUser(nextUser);
    setIsAuthReady(true);
    setSessionError("");
  }), [queryClient]);

  useEffect(() => {
    let active = true;
    setIsAuthReady(false);
    setSessionError("");
    void restoreSession()
      .then(() => {
        if (active) {
          setIsAuthReady(true);
        }
      })
      .catch((reason: unknown) => {
        if (active) setSessionError(reason instanceof Error ? reason.message : "登入狀態確認失敗，請稍後重試。");
      });
    return () => {
      active = false;
    };
  }, [sessionAttempt]);

  const login = async (email: string, password: string, rememberMe: boolean) => {
    const session = await apiFetch<AuthResponse>("/v1/auth/login", {
      method: "POST", body: JSON.stringify({ email, password, remember_me: rememberMe }),
    });
    saveSession(session);
    rememberLoginEmail(email, rememberMe);
  };

  const openLogin = () => {
    if (window.location.pathname === "/login") return;
    const returnTo = loginReturnTo(`${window.location.pathname}${window.location.search}${window.location.hash}`);
    window.location.assign(`/login?return_to=${encodeURIComponent(returnTo)}`);
  };

  const logout = async () => {
    try {
      await apiFetch<void>("/v1/auth/logout", { method: "POST" });
    } finally {
      clearSession();
    }
  };

  return <AuthContext.Provider value={{ user, isAuthReady, sessionError, sessionScope,
    retrySession: () => setSessionAttempt((value) => value + 1), openLogin, login, logout, saveMobileBarcode }}>
    {children}
  </AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("AuthProvider is missing");
  return context;
}
