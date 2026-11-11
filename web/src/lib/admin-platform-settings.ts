import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { StorefrontStatus } from "../components/StorefrontStatus";
import type { AdminPanelAccess } from "./admin-panel-access";
import { ApiError } from "./api";
import { emptyPublicInformation, type PublicInformation } from "./public-information";

interface SettingsFields {
  orders_open: boolean;
  support_phone: string;
  closed_message: string;
  printer_brand: string;
  printer_model: string;
  printer_connection: "unknown" | "browser" | "usb" | "network" | "bluetooth";
  kitchen_sound: boolean;
  public_information: PublicInformation;
}

type PlatformSettings = StorefrontStatus & SettingsFields & {
  version?: number;
  invoice_provider?: string;
};

export const adminPlatformQueryKey = ["admin-platform"] as const;
const path = "/v1/admin/platform";
const defaults: SettingsFields = {
  orders_open: true,
  support_phone: "",
  closed_message: "目前暫停接受新訂單",
  printer_brand: "",
  printer_model: "",
  printer_connection: "unknown",
  kitchen_sound: false,
  public_information: emptyPublicInformation,
};

function fieldsFrom(value?: PlatformSettings): SettingsFields {
  return {
    orders_open: value?.orders_open ?? defaults.orders_open,
    support_phone: value?.support_phone ?? defaults.support_phone,
    closed_message: value?.closed_message ?? defaults.closed_message,
    printer_brand: value?.printer_brand ?? defaults.printer_brand,
    printer_model: value?.printer_model ?? defaults.printer_model,
    printer_connection: value?.printer_connection ?? defaults.printer_connection,
    kitchen_sound: value?.kitchen_sound ?? defaults.kitchen_sound,
    public_information: value?.public_information ?? defaults.public_information,
  };
}

export function useAdminPlatformSettings(access: AdminPanelAccess, enabled: boolean, reason: string) {
  const client = useQueryClient();
  const settings = useQuery({
    queryKey: adminPlatformQueryKey,
    queryFn: ({ signal }) => access.request<PlatformSettings>(path, { signal }),
    enabled: enabled && !access.denied,
    retry: (count) => !access.isDenied() && count < 1,
  });
  const [editing, setEditing] = useState<{ fields: SettingsFields; version?: number } | null>(null);
  const [reloading, setReloading] = useState(false);
  const latest = fieldsFrom(settings.data);
  const draft = editing?.fields ?? latest;
  const latestVersion = settings.data?.version;
  const version = editing ? editing.version : latestVersion;
  const save = useMutation({
    mutationFn: ({ fields, version }: { fields: SettingsFields; version: number }) => access.request<PlatformSettings>(path, {
      method: "PUT",
      // The API replaces the settings record. Keep fields from the other page in the same versioned snapshot.
      body: JSON.stringify({ ...fields, expected_version: version, reason }),
    }),
    retry: false,
    onSuccess: (value) => {
      if (access.isDenied()) return;
      client.setQueryData<PlatformSettings>(adminPlatformQueryKey, (previous) => ({ ...previous, ...value }));
      setEditing(null);
      void client.invalidateQueries({ queryKey: ["public-platform"] });
    },
  });
  const versionReady = latestVersion !== undefined && Number.isInteger(latestVersion) && latestVersion >= 0;
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const changed = editing !== null && versionReady && latestVersion !== version;
  const busy = save.isPending || reloading;
  const canSave = !busy && !conflict && !changed && versionReady && !access.denied;

  function edit(fields: Partial<SettingsFields>) {
    const next = { ...draft, ...fields };
    setEditing(JSON.stringify(next) === JSON.stringify(latest) ? null : { fields: next, version });
    if (!conflict) save.reset();
  }

  async function reload() {
    setReloading(true);
    try {
      const result = await settings.refetch();
      if (result.data && !result.isError) {
        setEditing(null);
        save.reset();
      }
    } finally {
      setReloading(false);
    }
  }

  function submit() {
    if (canSave && version !== undefined && !access.isDenied()) save.mutate({ fields: draft, version });
  }

  return { settings, draft, edit, save, busy, canSave, changed, conflict, versionReady, reload, submit };
}
