import { IdentificationCard, PencilSimple, UserCircle } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";

import { SocialNav } from "../components/SocialNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import type { MemberDirectoryEntry, MembershipSummary } from "../lib/types";

const emptyForm = { is_public: false, nickname: "", avatar_url: "", expertise: "", bio: "" };

export function DirectoryPage() {
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const canAccess = membership.data?.membership_type === "member";
  const directory = useQuery({
    queryKey: ["member-directory"],
    queryFn: () => apiFetch<MemberDirectoryEntry[]>("/v1/members/directory"),
    enabled: canAccess,
  });
  const mine = directory.data?.find((entry) => entry.user_id === user?.id);

  useEffect(() => {
    const source = mine || membership.data?.directory;
    if (!source) return;
    setForm({
      is_public: Boolean(source.is_public),
      nickname: source.nickname || user?.display_name || "",
      avatar_url: mine?.avatar_url || "",
      expertise: source.expertise || "",
      bio: source.bio || "",
    });
  }, [mine, membership.data?.directory, user?.display_name]);

  const saveProfile = useMutation({
    mutationFn: () => apiFetch<MemberDirectoryEntry>("/v1/members/me/directory", {
      method: "PUT",
      body: JSON.stringify({ ...form, avatar_url: form.avatar_url || null }),
    }),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["member-directory"] });
      queryClient.invalidateQueries({ queryKey: ["membership-me"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    saveProfile.mutate();
  }

  return (
    <section className="social-page-shell">
      <header className="social-page-heading">
        <div><p className="eyebrow">MEMBER DIRECTORY</p><h1>社員名錄</h1></div>
        {canAccess && <button className="button button-system" type="button" onClick={() => setEditing((value) => !value)}><PencilSimple size={18} />編輯我的資料</button>}
      </header>
      <SocialNav />

      {!user ? (
        <div className="social-access-card"><IdentificationCard size={38} weight="light" /><h2>登入後查看社員名錄</h2><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></div>
      ) : membership.isPending ? <LoadingLines count={3} /> : !canAccess ? (
        <div className="social-access-card"><IdentificationCard size={38} weight="light" /><h2>此功能開放給正式社員</h2><Link className="button button-system" to="/membership">查看入社進度</Link></div>
      ) : (
        <>
          {editing && (
            <form className="social-form directory-form" onSubmit={submit}>
              <div className="form-heading"><div><p className="eyebrow">MY DIRECTORY PROFILE</p><h2>我的公開資料</h2></div><label className="switch-field"><input type="checkbox" checked={form.is_public} onChange={(event) => setForm({ ...form, is_public: event.target.checked })} /><span>{form.is_public ? "公開中" : "不公開"}</span></label></div>
              <div className="field-grid two-columns">
                <label className="field"><span>顯示名稱</span><input required maxLength={80} value={form.nickname} onChange={(event) => setForm({ ...form, nickname: event.target.value })} /></label>
                <label className="field"><span>頭像網址</span><input type="url" maxLength={500} placeholder="https://" value={form.avatar_url} onChange={(event) => setForm({ ...form, avatar_url: event.target.value })} /></label>
                <label className="field wide"><span>專長</span><input maxLength={240} value={form.expertise} onChange={(event) => setForm({ ...form, expertise: event.target.value })} /></label>
                <label className="field wide"><span>自我介紹</span><textarea rows={5} maxLength={2000} value={form.bio} onChange={(event) => setForm({ ...form, bio: event.target.value })} /></label>
              </div>
              {saveProfile.isError && <p className="form-error">{saveProfile.error.message}</p>}
              <div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setEditing(false)}>取消</button><button className="button button-primary" disabled={saveProfile.isPending}>儲存資料</button></div>
            </form>
          )}

          <div className="directory-summary"><span>{directory.data?.length ?? 0}</span><p>位社員選擇公開資料</p></div>
          {directory.isPending && <LoadingLines count={4} />}
          {directory.isError && <DataState kind="error" title="名錄暫時無法讀取" detail={directory.error.message} />}
          {directory.data?.length === 0 && <DataState title="目前沒有公開資料" detail="社員可自行決定是否出現在名錄中。" />}
          <div className="directory-grid">
            {directory.data?.map((entry) => (
              <article key={entry.user_id}>
                <div className="directory-avatar">{entry.avatar_url ? <img src={entry.avatar_url} alt="" /> : <UserCircle size={44} weight="light" />}</div>
                <small>{entry.user_id === user.id ? "這是你" : "社員"}</small>
                <h2>{entry.nickname}</h2>
                {entry.expertise && <strong>{entry.expertise}</strong>}
                <p>{entry.bio || "尚未填寫自我介紹。"}</p>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
