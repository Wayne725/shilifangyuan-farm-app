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
import { useQueryClient } from "@tanstack/react-query";

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
  refreshUser: () => Promise<User | null>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);
  const sessionRef = useRef<AuthSession | null>(null);

  const apply = useCallback((next: AuthSession | null) => {
    const previousUserId = sessionRef.current?.user.id ?? null;
    const nextUserId = next?.user.id ?? null;
    if (previousUserId !== nextUserId) queryClient.clear();
    sessionRef.current = next;
    setSession(next);
    setApiSession(next);
    void storeSession(next);
  }, [queryClient]);

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

  const login = useCallback(
    async (email: string, password: string) => {
      const nextSession = await api.login(email.trim(), password);
      apply(nextSession);
      return nextSession.user;
    },
    [apply],
  );

  const refreshUser = useCallback(async () => {
    const current = sessionRef.current;
    if (!current) return null;
    const expectedUserId = current.user.id;
    const user = await api.me();
    const latest = sessionRef.current;
    if (
      !latest ||
      latest.user.id !== expectedUserId ||
      user.id !== expectedUserId
    ) {
      return null;
    }
    apply({ ...latest, user });
    return user;
  }, [apply]);

  const logout = useCallback(() => apply(null), [apply]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user ?? null,
      token: session?.access_token ?? null,
      isAuthenticated: Boolean(session),
      isAdmin: session?.user.user_role === "admin",
      isRestoring,
      login,
      refreshUser,
      logout,
    }),
    [session, isRestoring, login, refreshUser, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth 必須在 AuthProvider 內使用");
  return context;
}
