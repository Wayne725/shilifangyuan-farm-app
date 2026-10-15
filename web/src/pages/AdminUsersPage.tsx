import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDateTime } from "../lib/api";
import "../styles/admin-users.css";

interface RegisteredUser {
  id: string; customer_number?: string | null; display_name: string; email: string | null; notification_email?: string | null;
  user_role: string; is_active: boolean; created_at: string; email_verified: boolean;
  membership_type: string; membership_status?: string | null; member_number?: string | null;
  membership_id?: string | null;
}
interface UserPage { items: RegisteredUser[]; total: number }
const statusLabels: Record<string, string> = { active: "正式社員", trainee: "實習社員", pending_payment: "待繳費", suspended: "非社員（會籍暫停）", resigned: "已退出", terminated: "已終止" };
const today = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Taipei" }).format(new Date());

export function AdminUsersPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const [draftSearch, setDraftSearch] = useState("");
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("all");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ search, membership_type: kind, limit: "20", offset: String(offset) });
  const users = useQuery({ queryKey: ["admin-registered-users", search, kind, offset], queryFn: () => apiFetch<UserPage>(`/v1/admin/users?${params}`), enabled: isAdmin });
  useEffect(() => { if (users.data && offset > 0 && offset >= users.data.total) setOffset(users.data.total ? Math.floor((users.data.total - 1) / 20) * 20 : 0); }, [users.data, offset]);
  if (!isAdmin) return <section className="admin-gate"><h1>會員管理</h1><p>請使用管理員帳號登入。</p>{!user ? <button type="button" className="button button-primary" onClick={openLogin}>管理員登入</button> : <Link to="/">返回首頁</Link>}</section>;
  return <section className="admin-module-page admin-users-page">
    <header className="workspace-heading"><div><p className="eyebrow">MEMBERS / 會員</p><h1>會員管理</h1></div></header><AdminNav />
    <form className="admin-user-search" onSubmit={(event) => { event.preventDefault(); setSearch(draftSearch.trim()); setOffset(0); }}>
      <label className="field"><span>姓名、Email、會員或社員編號</span><input maxLength={100} value={draftSearch} onChange={(event) => setDraftSearch(event.target.value)} /></label>
      <label className="field"><span>會員身分</span><select value={kind} onChange={(event) => { setKind(event.target.value); setOffset(0); }}><option value="all">所有註冊者</option><option value="member">正式社員</option><option value="nonmember">非社員</option><option value="trainee">實習社員</option></select></label>
      <button className="button button-system" type="submit">查詢會員</button>
    </form>
    {users.isPending && <LoadingLines count={3} />}{users.isError && <DataState kind="error" title="會員清單無法讀取" detail={users.error.message} onAction={() => users.refetch()} />}
    {users.data && <p role="status">共 {users.data.total} 位註冊者</p>}
    {users.data?.items.length === 0 && <DataState title="沒有符合條件的會員" detail="請調整姓名、編號或身分條件。" />}
    <div className="admin-user-list">{users.data?.items.map((item) => <RegisteredUserRow key={item.id} item={item} />)}</div>
    {users.data && <nav className="admin-pagination" aria-label="會員分頁"><button className="button button-quiet" type="button" disabled={offset === 0 || users.isFetching} onClick={() => setOffset(Math.max(0, offset - 20))}>上一頁</button><button className="button button-quiet" type="button" disabled={offset + 20 >= users.data.total || users.isFetching} onClick={() => setOffset(offset + 20)}>下一頁</button></nav>}
  </section>;
}

function RegisteredUserRow({ item }: { item: RegisteredUser }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [memberNumber, setMemberNumber] = useState(item.member_number || "");
  const [confirmedOn, setConfirmedOn] = useState(today);
  const [confirmed, setConfirmed] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeReason, setRemoveReason] = useState("社員資料誤設");
  const refreshMembership = () => Promise.all(["admin-registered-users", "admin-members", "membership-me", "admin-member-roster", "admin-membership-applications"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })));
  const remove = useMutation({
    mutationFn: () => apiFetch(`/v1/admin/members/${item.membership_id}/suspend`, { method: "POST", body: JSON.stringify({ reason: removeReason.trim() }) }),
    onSuccess: async () => { setRemoveOpen(false); await refreshMembership(); },
  });
  const grant = useMutation({
    mutationFn: () => apiFetch<RegisteredUser>(`/v1/admin/users/${item.id}/offline-membership`, { method: "POST", body: JSON.stringify({ confirmed_offline: confirmed, confirmed_on: confirmedOn, reason: reason.trim(), member_number: memberNumber.trim() || null }) }),
    onSuccess: async () => { setOpen(false); await Promise.all([queryClient.invalidateQueries({ queryKey: ["admin-registered-users"] }), queryClient.invalidateQueries({ queryKey: ["admin-members"] }), queryClient.invalidateQueries({ queryKey: ["admin-member-roster"] }), queryClient.invalidateQueries({ queryKey: ["admin-membership-applications"] })]); },
  });
  const canGrant = item.is_active && !["active", "resigned", "terminated"].includes(item.membership_status || "");
  const submit = (event: FormEvent) => { event.preventDefault(); if (confirmed && reason.trim().length >= 2) grant.mutate(); };
  return <article className="admin-user-row"><header><div><h2>{item.display_name}</h2><p>{item.email || "LINE登入帳號"}</p>{item.notification_email && <p>通知信箱：{item.notification_email}</p>}</div><strong>{statusLabels[item.membership_status || ""] || "非社員"}</strong></header>
    <p>會員編號：{item.customer_number || "尚未編號"}{item.member_number && `　社員編號：${item.member_number}`}</p>
    <p>註冊時間：{formatDateTime(item.created_at)}　帳號：{item.is_active ? "啟用" : "停用"}</p>
    <p>管理權限：{item.user_role === "admin" ? "管理員，可使用所有管理模組" : "一般使用者"}</p>
    {item.membership_status === "active" && item.membership_id && <button className="button button-quiet" type="button" onClick={() => { setRemoveOpen(!removeOpen); remove.reset(); }}>移除社員資格</button>}
    {removeOpen && <section className="admin-membership-removal" aria-label={`移除 ${item.display_name} 的社員資格`}>
      <h3>確認將 {item.display_name} 改為非社員？</h3>
      <div className="admin-quick-buttons" role="group" aria-label="移除資格原因">{["社員資料誤設", "社員資格異動"].map((value) => <button key={value} className="button button-quiet" type="button" aria-pressed={removeReason === value} onClick={() => setRemoveReason(value)}>{value}</button>)}</div>
      <label className="field"><span>處理原因</span><input required maxLength={1000} value={removeReason} onChange={(event) => setRemoveReason(event.target.value)} /></label>
      <p>保留帳號、訂單與股金紀錄，不會自動退款。</p>
      {remove.isError && <p className="form-error" role="alert">{remove.error.message}</p>}
      <div className="admin-user-actions"><button className="button button-danger" type="button" disabled={remove.isPending || !removeReason.trim()} onClick={() => remove.mutate()}>{remove.isPending ? "處理中…" : "確認改為非社員"}</button><button className="button button-quiet" type="button" disabled={remove.isPending} onClick={() => setRemoveOpen(false)}>取消</button></div>
    </section>}
    {remove.isSuccess && <p role="status">已改為非社員。</p>}
    {canGrant && <button className="button button-system" type="button" onClick={() => { setOpen(!open); grant.reset(); remove.reset(); setRemoveOpen(false); }}>{item.membership_status === "suspended" ? "核對並恢復社員資格" : "線下確認並設為社員"}</button>}
    {open && <form className="admin-offline-membership" onSubmit={submit}>
      <label className="field"><span>線下確認日期</span><input type="date" required max={today()} value={confirmedOn} onChange={(event) => setConfirmedOn(event.target.value)} /></label>
      <label className="field"><span>社員編號（已有就填入，否則自動編號）</span><input maxLength={32} readOnly={Boolean(item.member_number)} value={memberNumber} onChange={(event) => setMemberNumber(event.target.value)} /></label>
      <label className="field"><span>核對說明／收據參考</span><textarea required minLength={2} maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <label className="admin-offline-confirm"><input type="checkbox" required checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>我已核對本人身分及線下入社／繳費資料。</span></label>
      <p>會保留確認人、日期與說明；既有線上收退款案件需先核對，不會將線下繳費改寫成線上扣款。</p>
      {grant.isError && <p className="form-error" role="alert">{grant.error.message}</p>}
      <button className="button button-primary" disabled={grant.isPending || !confirmed || reason.trim().length < 2}>{grant.isPending ? "確認中…" : "確認授予正式社員資格"}</button>
    </form>}
    {grant.isSuccess && <p role="status">社員資格已啟用。</p>}
  </article>;
}
