import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { ApiError, apiFetch } from "./api";

const privateQueryKeys = [["admin-failed-jobs"], ["admin-reconciliation-status"]];

export function useAdminPanelAccess() {
  const client = useQueryClient();
  const deniedRef = useRef(false);
  const [denied, setDenied] = useState(false);
  const isDenied = useCallback(() => deniedRef.current, []);
  const request = useCallback(async <T>(path: string, options: RequestInit = {}): Promise<T> => {
    if (deniedRef.current) throw new Error("管理權限待重新驗證。");
    try {
      const result = await apiFetch<T>(path, options);
      // A response started before another request confirmed denial cannot restore private data.
      if (deniedRef.current) throw new Error("管理權限待重新驗證。");
      return result;
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.status) && !deniedRef.current) {
        deniedRef.current = true;
        setDenied(true);
        for (const queryKey of privateQueryKeys) {
          void client.cancelQueries({ queryKey });
          client.removeQueries({ queryKey });
        }
      }
      throw error;
    }
  }, [client]);
  return { denied, isDenied, request };
}

export type AdminPanelAccess = ReturnType<typeof useAdminPanelAccess>;
