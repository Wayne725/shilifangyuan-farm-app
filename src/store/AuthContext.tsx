import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { loadStoredSession, storeSession } from "../lib/sessionStorage";
import { api, setApiSession, setSessionHandlers } from "../services/api";
import type { AuthSession, User } from "../types";

type AuthContextValue = {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  /** False until the persisted session has been read back on startup. */
  isRestoring: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);
  const sessionRef = useRef<AuthSession | null>(null);

  const apply = useCallback((next: AuthSession | null) => {
    sessionRef.current = next;
    setSession(next);
    setApiSession(next);
    void storeSession(next);
  }, []);

  useEffect(() => {
    setSessionHandlers({
      // Refreshing rotates both tokens; keep the stored copy in step.
      onRefreshed: (renewed) =>
        apply({ ...(sessionRef.current ?? renewed), ...renewed }),
      onExpired: () => apply(null),
    });
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = await loadStoredSession();
      if (cancelled) return;
      if (!stored) {
        setIsRestoring(false);
        return;
      }
      sessionRef.current = stored;
      setApiSession(stored);
      try {
        // Confirms the token still works and picks up membership changes made
        // while the app was closed; a 401 here triggers the refresh path.
        const user = await api.me();
        if (cancelled) return;
        apply({ ...sessionRef.current!, user });
      } catch {
        if (!cancelled) apply(null);
      } finally {
        if (!cancelled) setIsRestoring(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const login = async (email: string, password: string) => {
    const nextSession = await api.login(email.trim(), password);
    apply(nextSession);
    return nextSession.user;
  };

  const logout = () => apply(null);

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user ?? null,
      token: session?.access_token ?? null,
      isAuthenticated: Boolean(session),
      isAdmin: session?.user.user_role === "admin",
      isRestoring,
      login,
      logout,
    }),
    [session, isRestoring],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth 必須在 AuthProvider 內使用");
  return context;
}
