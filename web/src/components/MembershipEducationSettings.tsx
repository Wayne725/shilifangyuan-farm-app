import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DataState, LoadingLines } from "./Shared";
import { ApiError, apiFetch } from "../lib/api";

interface EducationSettings {
  configured: boolean;
  version: number;
  retry_limit: number;
  retry_interval_minutes: number;
  updated_at: string | null;
  updated_by_id: string | null;
}
interface SettingsDraft {
  version: number;
  retry_limit: string;
  retry_interval_minutes: string;
  reason: string;
}
const queryKey = ["admin-membership-education-settings"];
const settingsPath = "/v1/admin/membership-education/settings";
const asDraft = (value: EducationSettings): SettingsDraft => ({
  version: value.version,
  retry_limit: value.configured ? String(value.retry_limit) : "",
  retry_interval_minutes: value.configured ? String(value.retry_interval_minutes) : "",
  reason: "",
});

export function MembershipEducationSettings() {
  const client = useQueryClient();
  const settings = useQuery({
    queryKey,
    queryFn: () => apiFetch<EducationSettings>(settingsPath),
  });
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [reloading, setReloading] = useState(false);
  useEffect(() => {
    if (!draft && settings.data) setDraft(asDraft(settings.data));
  }, [draft, settings.data]);
  const save = useMutation({
    mutationFn: (value: SettingsDraft) => apiFetch<EducationSettings>(settingsPath, {
      method: "PUT",
      body: JSON.stringify({
        expected_version: value.version,
        retry_limit: Number(value.retry_limit),
        retry_interval_minutes: Number(value.retry_interval_minutes),
        reason: value.reason.trim(),
      }),
    }),
    onSuccess: (value) => {
      client.setQueryData(queryKey, value);
      setDraft(asDraft(value));
    },
  });
  function edit(change: Partial<SettingsDraft>) {
    if (draft) setDraft({ ...draft, ...change });
    if (!(save.error instanceof ApiError && save.error.status === 409)) save.reset();
  }
  async function reload() {
    setReloading(true);
    try {
      const result = await settings.refetch();
      if (result.data && !result.isError) {
        setDraft(asDraft(result.data));
        save.reset();
      }
    } finally {
      setReloading(false);
    }
  }
  const wholeNumber = (value: string, max: number) => /^\d+$/.test(value) && Number(value) <= max;
  const valid = draft && wholeNumber(draft.retry_limit, 100)
    && wholeNumber(draft.retry_interval_minutes, 525600);
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const busy = save.isPending || reloading;
  return <form onSubmit={event => { event.preventDefault(); if (valid && draft) save.mutate(draft); }}>
    <section className="admin-settings-section" aria-labelledby="membership-education-heading">
      <p className="eyebrow">MEMBERSHIP EDUCATION / 入社教育</p>
      <h2 id="membership-education-heading">測驗重考設定</h2>
      {settings.isPending ? <LoadingLines count={3} /> : settings.isError ?
        <DataState title="重考設定無法讀取" detail={settings.error.message} /> : draft && <>
        <p>{settings.data?.configured ? `已儲存設定 · 第 ${settings.data.version} 版` : "尚未設定"}</p>
        <fieldset className="membership-education-fields" aria-labelledby="membership-education-heading" disabled={busy}>
          <div className="field-grid two-columns">
            <label className="field"><span>額外重考次數（不含首次）</span><input type="number" min={0} max={100} step={1} required value={draft.retry_limit} onChange={event => edit({ retry_limit: event.target.value })} /></label>
            <label className="field"><span>重考間隔（分鐘）</span><input type="number" min={0} max={525600} step={1} required value={draft.retry_interval_minutes} onChange={event => edit({ retry_interval_minutes: event.target.value })} /></label>
          </div>
          <label className="field"><span>調整原因</span><input maxLength={1000} value={draft.reason} onChange={event => edit({ reason: event.target.value })} /></label>
        </fieldset>
        {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
        {save.isSuccess && <p className="form-success" role="status">重考設定已儲存</p>}
        <div className="form-actions">
          <button className="button button-primary" disabled={!valid || busy || conflict}>{save.isPending ? "儲存中…" : "儲存重考設定"}</button>
          {(conflict || settings.data?.version !== draft.version) && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void reload()}>重新讀取最新設定</button>}
        </div>
      </>}
      {settings.isError && <button type="button" className="button button-quiet" disabled={settings.isFetching} onClick={() => void settings.refetch()}>重新讀取</button>}
    </section>
  </form>;
}
