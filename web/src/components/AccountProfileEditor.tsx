import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch } from "../lib/api";
import { contactPhoneIsValid } from "../lib/contact-phone";

type Profile = { display_name: string; phone: string | null; is_active: boolean };

export function AccountProfileEditor({ userId, adminTarget, request = apiFetch, onSave }: {
  userId: string;
  adminTarget?: string;
  request?: typeof apiFetch;
  onSave?: (profile: Profile) => void;
}) {
  const [open, setOpen] = useState(false);
  const client = useQueryClient();
  const path = adminTarget ? `/v1/admin/users/${adminTarget}/profile` : "/v1/auth/profile";
  const key = ["account-profile", userId, adminTarget];
  const profile = useQuery({ queryKey: key, queryFn: ({ signal }) => request<Profile>(path, { signal }),
    enabled: open, retry: false, gcTime: 0, refetchOnWindowFocus: false });
  return <section>
    <button className="button button-quiet" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? "收起帳號資料" : "編輯帳號資料"}
    </button>
    {open && (profile.isFetching ? <p role="status">讀取帳號資料…</p> : profile.error ?
      <p className="form-error" role="alert">{profile.error.message}</p> : profile.data &&
      <ProfileForm profile={profile.data} canChangeStatus={Boolean(adminTarget && adminTarget !== userId)}
        request={request} path={path} userId={userId} onSave={async saved => {
          client.setQueryData(key, saved);
          if (adminTarget) await client.invalidateQueries({ queryKey: ["admin-registered-users"] });
          void client.invalidateQueries({ queryKey: ["meal-checkout-contact", adminTarget || userId] });
          onSave?.(saved);
          setOpen(false);
        }} />)}
  </section>;
}

function ProfileForm({ profile, canChangeStatus, request, path, userId, onSave }: {
  profile: Profile; canChangeStatus: boolean; request: typeof apiFetch; path: string; userId: string;
  onSave: (profile: Profile) => Promise<void>;
}) {
  const [name, setName] = useState(profile.display_name);
  const [phone, setPhone] = useState(profile.phone || "");
  const [active, setActive] = useState(profile.is_active);
  const [confirmed, setConfirmed] = useState(false);
  const disabling = profile.is_active && !active;
  const valid = Boolean(name.trim()) && (!phone.trim() ? !profile.phone : contactPhoneIsValid(phone));
  const save = useMutation({
    mutationFn: () => request<Profile>(path, {
      method: "PATCH", headers: { "X-Expected-User-ID": userId },
      body: JSON.stringify({ display_name: name.trim(), ...(phone.trim() ? { phone: phone.trim() } : {}),
        ...(canChangeStatus ? { is_active: active } : {}) }),
    }),
    onSuccess: onSave,
  });
  return <form className="social-form" aria-label="編輯帳號資料" onSubmit={event => {
    event.preventDefault();
    if (valid && (!disabling || confirmed) && !save.isPending) save.mutate();
  }}>
    <div className="field-grid two-columns">
      <label className="field"><span>姓名</span><input required maxLength={80} value={name} onChange={event => setName(event.target.value)} /></label>
      <label className="field"><span>聯絡電話</span><input type="tel" required={Boolean(profile.phone)} autoComplete="tel" maxLength={40} value={phone} onChange={event => setPhone(event.target.value)} /></label>
      {canChangeStatus && <label className="field"><span>帳號狀態</span><select value={active ? "active" : "inactive"}
        onChange={event => { setActive(event.target.value === "active"); setConfirmed(false); }}>
        <option value="active">啟用</option><option value="inactive">停用</option>
      </select></label>}
    </div>
    {disabling && <label className="check-field"><input type="checkbox" checked={confirmed}
      onChange={event => setConfirmed(event.target.checked)} />確認停用登入與下單</label>}
    {phone.trim() && !contactPhoneIsValid(phone) && <p className="form-error">請輸入完整手機號碼或含區碼的市話</p>}
    {save.error && <p className="form-error" role="alert">{save.error.message}</p>}
    <button className="button button-system" disabled={save.isPending || !valid || (disabling && !confirmed)}>儲存帳號資料</button>
  </form>;
}
