import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, apiFetch, formatMoney } from "../lib/api";
import { DataState, LoadingLines } from "./Shared";
import "./MembershipEducationSettings.css";

interface FeeSettings {
  admission_fee_amount: number;
  version: number;
  share_unit_amount: number;
  maximum_admission_fee_amount: number;
}

interface FeeDraft {
  amount: string;
  version: number;
  reason: string;
}

const settingsPath = "/v1/admin/membership/admission-fee";
const asDraft = (settings: FeeSettings): FeeDraft => ({
  amount: String(settings.admission_fee_amount),
  version: settings.version,
  reason: "",
});
const accessDenied = (error: unknown) => error instanceof ApiError && (error.status === 401 || error.status === 403);

export function MembershipAdmissionFeeSettings({ adminId }: { adminId: string }) {
  const [open, setOpen] = useState(false);
  return <details className="membership-education-settings" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>新申請入社費</summary>
    {open && <AdmissionFeeForm key={adminId} adminId={adminId} />}
  </details>;
}

function AdmissionFeeForm({ adminId }: { adminId: string }) {
  const client = useQueryClient();
  const queryKey = ["admin-membership-admission-fee", adminId];
  const settings = useQuery({
    queryKey,
    queryFn: ({ signal }) => apiFetch<FeeSettings>(settingsPath, { signal, cache: "no-store" }),
    refetchOnWindowFocus: false,
    retry: (count, error) => !accessDenied(error) && count < 1,
  });
  const [draft, setDraft] = useState<FeeDraft | null>(null);
  const save = useMutation({
    mutationFn: (value: FeeDraft) => apiFetch<FeeSettings>(settingsPath, {
      method: "PUT",
      body: JSON.stringify({
        admission_fee_amount: Number(value.amount),
        expected_version: value.version,
        reason: value.reason.trim(),
      }),
    }),
    onSuccess: (value) => {
      client.setQueryData(queryKey, value);
      setDraft(null);
      void client.invalidateQueries({ queryKey: ["membership-admission"] });
    },
  });
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const revoked = accessDenied(settings.error) || accessDenied(save.error);
  if (revoked) return <DataState kind="error" title="入社費設定權限已失效" detail="請重新登入管理員帳號。" />;
  if (settings.isPending) return <LoadingLines count={3} />;
  if (settings.isError) return <DataState kind="error" title="入社費設定無法讀取" detail={settings.error.message} onAction={() => void settings.refetch()} />;
  const current = settings.data;
  const value = draft ?? asDraft(current);
  const valid = /^\d+$/.test(value.amount) && Number(value.amount) <= current.maximum_admission_fee_amount;
  const busy = settings.isFetching || save.isPending;
  const stale = conflict || value.version !== current.version;

  function edit(change: Partial<FeeDraft>) {
    setDraft({ ...value, ...change });
    if (!conflict) save.reset();
  }

  async function reload() {
    const latest = await settings.refetch();
    if (!latest.isError) {
      setDraft(null);
      save.reset();
    }
  }

  return <form onSubmit={(event) => {
    event.preventDefault();
    if (valid && !busy && !stale) save.mutate(value);
  }}>
    <p>新建立的申請採用此金額。每股股金 {formatMoney(current.share_unit_amount)}。</p>
    <fieldset className="membership-education-fields" disabled={busy}>
      <div className="field-grid two-columns">
        <label className="field"><span>入社費（元）</span><input type="number" inputMode="numeric" min={0} max={current.maximum_admission_fee_amount} step={1} required value={value.amount} onChange={(event) => edit({ amount: event.target.value })} /></label>
        <label className="field"><span>調整原因</span><input value={value.reason} maxLength={1000} onChange={(event) => edit({ reason: event.target.value })} /></label>
      </div>
    </fieldset>
    {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
    {save.isSuccess && <p className="form-success" role="status">入社費設定已儲存。</p>}
    <div className="form-actions">
      <button className="button button-primary" disabled={!valid || busy || stale || Number(value.amount) === current.admission_fee_amount}>{save.isPending ? "儲存中…" : "儲存入社費"}</button>
      {stale && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload()}>重新讀取入社費設定</button>}
    </div>
  </form>;
}
