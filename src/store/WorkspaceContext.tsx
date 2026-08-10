import { usePathname } from "expo-router";
import {
  createContext,
  type PropsWithChildren,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { Workspace } from "../types";

const socialRoutes = new Set([
  "/social-home",
  "/members",
  "/activities",
  "/member-proposals",
  "/social-profile",
  "/social-account",
  "/social-points",
  "/social-wishes",
  "/social-meetings",
  "/social-surplus",
]);
const lifeRoutes = new Set([
  "/home",
  "/group-buy",
  "/meals",
  "/orders",
  "/profile",
]);

type WorkspaceContextValue = {
  workspace: Workspace;
  setWorkspace: (workspace: Workspace) => void;
  lastRoute: Record<Workspace, string>;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: PropsWithChildren) {
  const pathname = usePathname();
  const [workspace, setWorkspace] = useState<Workspace>("life");
  const [lastRoute, setLastRoute] = useState<Record<Workspace, string>>({
    life: "/home",
    social: "/social-home",
  });

  useEffect(() => {
    if (socialRoutes.has(pathname)) {
      setWorkspace("social");
      setLastRoute((current) => ({ ...current, social: pathname }));
    } else if (lifeRoutes.has(pathname)) {
      setWorkspace("life");
      setLastRoute((current) => ({ ...current, life: pathname }));
    }
  }, [pathname]);

  const value = useMemo(
    () => ({ workspace, setWorkspace, lastRoute }),
    [lastRoute, workspace],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error("useWorkspace 必須在 WorkspaceProvider 內使用");
  }
  return context;
}
