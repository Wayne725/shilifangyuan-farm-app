import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
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
import { rememberLoginEmail } from "../lib/login-preferences";
import { loginReturnTo } from "../lib/login-navigation";
import "../styles/auth-options.css";

interface AuthContextValue {
  user: User | null;
  isAuthReady: boolean;
  sessionError: string;
  retrySession: () => void;
  openLogin: () => void;
  login: (email: string, password: string, rememberMe: boolean) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [sessionAttempt, setSessionAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setIsAuthReady(false);
    setSessionError("");
    void restoreSession()
      .then((session) => {
        if (active) {
          setUser(session?.user ?? null);
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
    queryClient.clear();
    setUser(session.user);
    setIsAuthReady(true);
    setSessionError("");
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
      queryClient.clear();
      setUser(null);
    }
  };

  return <AuthContext.Provider value={{ user, isAuthReady, sessionError,
    retrySession: () => setSessionAttempt((value) => value + 1), openLogin, login, logout }}>
    {children}
  </AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("AuthProvider is missing");
  return context;
}
