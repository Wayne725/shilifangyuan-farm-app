import {
  createContext,
  type PropsWithChildren,
  useContext,
  useMemo,
  useState,
} from "react";

import { api, setApiAccessToken } from "../services/api";
import type { AuthSession, User } from "../types";

type AuthContextValue = {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [session, setSession] = useState<AuthSession | null>(null);

  const login = async (email: string, password: string) => {
    const nextSession = await api.login(email.trim(), password);
    setApiAccessToken(nextSession.access_token);
    setSession(nextSession);
    return nextSession.user;
  };

  const logout = () => {
    setApiAccessToken(null);
    setSession(null);
  };

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user ?? null,
      token: session?.access_token ?? null,
      isAuthenticated: Boolean(session),
      isAdmin: session?.user.user_role === "admin",
      login,
      logout,
    }),
    [session],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth 必須在 AuthProvider 內使用");
  return context;
}
