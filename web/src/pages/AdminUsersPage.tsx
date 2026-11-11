import { Link, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { AdminMembershipPanel, AdminRosterPanel, useAdminMemberships } from "../components/AdminMemberPanels";
import { AdminNav } from "../components/AdminNav";
import { AdminRecordsPanel } from "../components/AdminRecordsPanel";
import { OptionalMark } from "../components/OptionalMark";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { useAdminPanelAccess } from "../lib/admin-panel-access";
import { ApiError, captureSessionIdentityGuard, downloadBlob, fetchApiBlob, formatDateTime, type apiFetch } from "../lib/api";
import "../styles/admin-users.css";

interface RegisteredUser {
  id: string; customer_number?: string | null; display_name: string; email: string | null; notification_email?: string | null;
  user_role: string; is_active: boolean; created_at: string; email_verified: boolean;
  membership_type: string; membership_status?: string | null; member_number?: string | null;
  membership_id?: string | null;
}
interface UserPage { items: RegisteredUser[]; total: number }
interface MembershipPhoneMatch { roster_entry_id: string; member_number: string; legal_name: string; phone_masked: string }
const statusLabels: Record<string, string> = { active: "正式社員", trainee: "實習社員", pending_payment: "待繳費", suspended: "非社員（會籍暫停）", resigned: "已退出", terminated: "已終止" };
const today = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Taipei" }).format(new Date());
const memberTabs = [
  { tab: undefined, label: "註冊帳號" },
  { tab: "members", label: "會籍管理" },
  { tab: "roster", label: "社員名冊" },
  { tab: "records", label: "社員帳冊" },
] as const;
const memberQueryKeys = [["admin-registered-users"], ["admin-members"], ["admin-member-roster"], ["member-register"], ["membership-payments"], ["points-ledger"]] as const;
const initialAccounts = { draftSearch: "", search: "", kind: "all", offset: 0 };
const membershipIdFormat = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export function AdminUsersPage() {
  const { user, openLogin, sessionScope } = useAuth();
  if (user?.user_role !== "admin") return <section className="admin-gate"><h1>會員管理</h1><p>請使用管理員帳號登入。</p>{!user ? <button type="button" className="button button-primary" onClick={openLogin}>管理員登入</button> : <Link to="/">返回首頁</Link>}</section>;
  return <AdminUsersWorkspace key={`${user.id}:${sessionScope}`} adminId={user.id} />;
}

function AdminUsersWorkspace({ adminId }: { adminId: string }) {
  const { tab, ledger, q, membership, member } = useSearch({ from: "/admin/users" });
  const [accounts, setAccounts] = useState(initialAccounts);
  const [sameIdentity] = useState(captureSessionIdentityGuard);
  const access = useAdminPanelAccess(memberQueryKeys);
  const { isDenied, request: requestWithAccess, run } = access;
  const requireAccess = useCallback(() => {
    if (isDenied() || !sameIdentity()) throw new ApiError(401, "請重新登入管理員帳號。");
  }, [isDenied, sameIdentity]);
  const request = useCallback<typeof apiFetch>(async (path, options) => {
    requireAccess();
    return requestWithAccess(path, options);
  }, [requireAccess, requestWithAccess]);
  const downloadFile = useCallback(async (path: string, filename: string) => {
    requireAccess();
    const blob = await run(() => fetchApiBlob(path));
    requireAccess();
    downloadBlob(blob, filename);
  }, [requireAccess, run]);
  const memberships = useAdminMemberships({ adminId, request }, tab === "members" && !access.denied);
  const legacyQuery = !membership && !member ? q?.toLowerCase() : undefined;
  const exactLegacy = legacyQuery ? memberships.data?.find(item => item.id.toLowerCase() === legacyQuery) : undefined;
  const byUser = legacyQuery ? memberships.data?.find(item => item.user_id.toLowerCase() === legacyQuery) : undefined;
  const selected = membership ? memberships.data?.find(item => item.id === membership) : exactLegacy || byUser;
  const missingLegacyId = legacyQuery && membershipIdFormat.test(legacyQuery) && !selected ? q : undefined;
  const scope = { membership: membership || selected?.id || missingLegacyId, member: member || selected?.member_number || undefined };
  const resolvingNumber = tab === "members" && Boolean(legacyQuery || (membership && !member)) && !memberships.data;
  const missingNumber = Boolean(membership && !member);

  if (access.denied) return <section className="admin-gate"><h1>會員管理</h1><p role="alert">管理權限已失效，請重新驗證。</p><button className="button button-system" type="button" onClick={() => window.location.reload()}>重新驗證管理權限</button></section>;
  return <section className="admin-module-page admin-users-page">
    <header className="workspace-heading"><div><p className="eyebrow">MEMBERS / 會員</p><h1>會員管理</h1></div></header>
    <AdminNav />
    <nav className="tab-list admin-member-tabs" aria-label="會員管理分類">
      {memberTabs.map(({ tab: value, label }) => resolvingNumber && (value === "roster" || value === "records")
        ? <span key={label} aria-disabled="true">{label}</span>
        : <Link key={label} to="/admin/users" search={value ? { tab: value, ...scope, q: value === "members" ? q : undefined } : {}}
          aria-current={tab === value ? "page" : undefined} data-state={tab === value ? "active" : undefined}>{label}</Link>)}
    </nav>
    {!tab && <RegisteredUsersPanel adminId={adminId} request={request} accounts={accounts} setAccounts={setAccounts} />}
    {tab === "members" && <AdminMembershipPanel key={JSON.stringify([scope.membership, scope.member, q])} adminId={adminId}
      request={request} memberships={memberships} membership={scope.membership} member={scope.member} q={scope.membership || scope.member ? undefined : q} />}
    {(tab === "roster" || tab === "records") && (missingNumber ? <div className="admin-member-unlinked">
      <DataState title="尚未選定社員編號" detail="請先查看這筆會籍。" />
      <div className="admin-quick-buttons">
        <Link className="button button-quiet" to="/admin/users" search={{ tab: "members", membership }}>查看會籍</Link>
        <Link className="text-link" to="/admin/users" search={{ tab, ledger }}>{tab === "roster" ? "查看全部名冊" : "查看全部帳冊"}</Link>
      </div>
    </div> : tab === "roster"
      ? <AdminRosterPanel key={member} adminId={adminId} request={request} member={member} />
      : <AdminRecordsPanel key={member} adminId={adminId} request={request} downloadFile={downloadFile}
        tab={ledger ?? "members"} member={member} membership={membership} />)}
  </section>;
}

function RegisteredUsersPanel({ adminId, request, accounts, setAccounts }: {
  adminId: string;
  request: typeof apiFetch;
  accounts: typeof initialAccounts;
  setAccounts: React.Dispatch<React.SetStateAction<typeof initialAccounts>>;
}) {
  const { draftSearch, search, kind, offset } = accounts;
  const params = new URLSearchParams({ search, membership_type: kind, limit: "20", offset: String(offset) });
  const users = useQuery({ queryKey: ["admin-registered-users", adminId, search, kind, offset], queryFn: ({ signal }) => request<UserPage>(`/v1/admin/users?${params}`, { signal }) });
  const total = users.data?.total;
  useEffect(() => {
    if (total !== undefined && offset > 0 && offset >= total) setAccounts(current => ({ ...current, offset: total ? Math.floor((total - 1) / 20) * 20 : 0 }));
  }, [total, offset, setAccounts]);
  return <section className="admin-registered-accounts" aria-label="註冊帳號">
    <form className="admin-user-search" onSubmit={(event) => { event.preventDefault(); setAccounts({ ...accounts, search: draftSearch.trim(), offset: 0 }); }}>
      <label className="field"><span>姓名、Email、會員或社員編號</span><input maxLength={100} value={draftSearch} onChange={(event) => setAccounts({ ...accounts, draftSearch: event.target.value })} /></label>
      <label className="field"><span>會員身分</span><select value={kind} onChange={(event) => setAccounts({ ...accounts, kind: event.target.value, offset: 0 })}><option value="all">所有註冊者</option><option value="member">正式社員</option><option value="nonmember">非社員</option><option value="trainee">實習社員</option></select></label>
      <button className="button button-system" type="submit">查詢會員</button>
    </form>
    {users.isPending && <LoadingLines count={3} />}{users.isError && <DataState kind="error" title="會員清單無法讀取" detail={users.error.message} onAction={() => users.refetch()} />}
    {users.data && <p role="status">共 {users.data.total} 位註冊者</p>}
    {users.data?.items.length === 0 && <DataState title="沒有符合條件的會員" detail="請調整姓名、編號或身分條件。" />}
    <div className="admin-user-list">{users.data?.items.map((item) => <RegisteredUserRow key={item.id} item={item} request={request} />)}</div>
    {users.data && <nav className="admin-pagination" aria-label="會員分頁"><button className="button button-quiet" type="button" disabled={offset === 0 || users.isFetching} onClick={() => setAccounts({ ...accounts, offset: Math.max(0, offset - 20) })}>上一頁</button><button className="button button-quiet" type="button" disabled={offset + 20 >= users.data.total || users.isFetching} onClick={() => setAccounts({ ...accounts, offset: offset + 20 })}>下一頁</button></nav>}
  </section>;
}

function RegisteredUserRow({ item, request }: { item: RegisteredUser; request: typeof apiFetch }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [phone, setPhone] = useState("");
  const [confirmedOn, setConfirmedOn] = useState(today);
  const [confirmed, setConfirmed] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeReason, setRemoveReason] = useState("社員資料誤設");
  const phoneCheck = useMutation({
    mutationFn: async (value: string) => ({
      phone: value,
      match: await request<MembershipPhoneMatch>(`/v1/admin/users/${item.id}/membership-phone-check`, { method: "POST", body: JSON.stringify({ phone: value }) }),
    }),
  });
  const phoneMatch = phoneCheck.data?.phone === phone.trim() ? phoneCheck.data.match : null;
  const refreshMembership = () => Promise.all(["admin-registered-users", "admin-members", "membership-me", "admin-member-roster", "admin-membership-applications"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })));
  const remove = useMutation({
    mutationFn: () => request(`/v1/admin/members/${item.membership_id}/suspend`, { method: "POST", body: JSON.stringify({ reason: removeReason.trim() }) }),
    onSuccess: async () => { setRemoveOpen(false); await refreshMembership(); },
  });
  const grant = useMutation({
    mutationFn: () => request<RegisteredUser>(`/v1/admin/users/${item.id}/offline-membership`, { method: "POST", body: JSON.stringify({ confirmed_offline: confirmed, confirmed_on: confirmedOn, reason: reason.trim(), phone: phone.trim(), roster_entry_id: phoneMatch?.roster_entry_id }) }),
    onError: (error) => { if (error instanceof ApiError && error.status === 409) { phoneCheck.reset(); setConfirmed(false); } },
    onSuccess: async () => { setOpen(false); await Promise.all([queryClient.invalidateQueries({ queryKey: ["admin-registered-users"] }), queryClient.invalidateQueries({ queryKey: ["admin-members"] }), queryClient.invalidateQueries({ queryKey: ["admin-member-roster"] }), queryClient.invalidateQueries({ queryKey: ["admin-membership-applications"] })]); },
  });
  const canGrant = item.is_active && !["active", "resigned", "terminated"].includes(item.membership_status || "");
  const scope = { membership: item.membership_id || undefined, member: item.member_number || undefined };
  const submit = (event: FormEvent) => { event.preventDefault(); if (phoneMatch && confirmed && !grant.isPending) grant.mutate(); };
  return <article className="admin-user-row"><header><div><h2>{item.display_name}</h2><p>{item.email || "LINE登入帳號"}</p>{item.notification_email && <p>通知信箱：{item.notification_email}</p>}</div><strong>{statusLabels[item.membership_status || ""] || "非社員"}</strong></header>
    <p>會員編號：{item.customer_number || "尚未編號"}{item.member_number && `　社員編號：${item.member_number}`}</p>
    {(item.membership_id || item.member_number) && <nav className="admin-quick-buttons" aria-label={`${item.display_name}的社員資料`}>
      {item.membership_id && <Link className="button button-quiet" to="/admin/users" search={{ tab: "members", ...scope }}>會籍異動</Link>}
      {item.member_number && <>
        <Link className="button button-quiet" to="/admin/users" search={{ tab: "roster", ...scope }}>社員名冊</Link>
        <Link className="button button-quiet" to="/admin/users" search={{ tab: "records", ...scope }}>社員帳冊</Link>
      </>}
    </nav>}
    <p>註冊時間：{formatDateTime(item.created_at)}　帳號：{item.is_active ? "啟用" : "停用"}</p>
    <p>管理權限：{item.user_role === "admin" ? "管理員，可使用所有管理模組" : "一般使用者"}</p>
    {item.membership_status === "active" && item.membership_id && <button className="button button-quiet" type="button" onClick={() => { setRemoveOpen(!removeOpen); remove.reset(); }}>移除社員資格</button>}
    {removeOpen && <section className="admin-membership-removal" aria-label={`移除 ${item.display_name} 的社員資格`}>
      <h3>確認將 {item.display_name} 改為非社員？</h3>
      <div className="admin-quick-buttons" role="group" aria-label="移除資格原因">{["社員資料誤設", "社員資格異動"].map((value) => <button key={value} className="button button-quiet" type="button" aria-pressed={removeReason === value} onClick={() => setRemoveReason(value)}>{value}</button>)}</div>
      <label className="field"><span>處理原因<OptionalMark /></span><input maxLength={1000} value={removeReason} onChange={(event) => setRemoveReason(event.target.value)} /></label>
      <p>保留帳號、訂單與股金紀錄，不會自動退款。</p>
      {remove.isError && <p className="form-error" role="alert">{remove.error.message}</p>}
      <div className="admin-user-actions"><button className="button button-danger" type="button" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "處理中…" : "確認改為非社員"}</button><button className="button button-quiet" type="button" disabled={remove.isPending} onClick={() => setRemoveOpen(false)}>取消</button></div>
    </section>}
    {remove.isSuccess && <p role="status">已改為非社員。</p>}
    {canGrant && <button className="button button-system" type="button" disabled={grant.isPending} onClick={() => { setOpen(!open); setPhone(""); setConfirmed(false); phoneCheck.reset(); grant.reset(); remove.reset(); setRemoveOpen(false); }}>{item.membership_status === "suspended" ? "核對並恢復社員資格" : "線下確認並設為社員"}</button>}
    {open && <form className="admin-offline-membership" onSubmit={submit}>
      <label className="field"><span>線下確認日期</span><input type="date" required max={today()} value={confirmedOn} onChange={(event) => setConfirmedOn(event.target.value)} /></label>
      <div className="admin-membership-phone-check">
        <label className="field"><span>手機號碼</span><input type="tel" inputMode="tel" autoComplete="off" required maxLength={32} disabled={grant.isPending} value={phone} onChange={(event) => { setPhone(event.target.value); setConfirmed(false); phoneCheck.reset(); grant.reset(); }} /></label>
        <button className="button button-system" type="button" disabled={!phone.trim() || phoneCheck.isPending || grant.isPending} onClick={() => { setConfirmed(false); grant.reset(); phoneCheck.mutate(phone.trim()); }}>{phoneCheck.isPending ? "核對中…" : "核對手機"}</button>
      </div>
      {phoneCheck.isError && <p className="form-error" role="alert">{phoneCheck.error.message}</p>}
      {phoneMatch && <section className="admin-membership-phone-result" aria-label="手機核對結果" role="status"><dl>
        <div><dt>名冊姓名</dt><dd>{phoneMatch.legal_name}</dd></div>
        <div><dt>社員編號</dt><dd>{phoneMatch.member_number}</dd></div>
        <div><dt>手機</dt><dd>{phoneMatch.phone_masked}</dd></div>
      </dl></section>}
      <label className="field"><span>核對說明／收據參考<OptionalMark /></span><textarea maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <label className="admin-offline-confirm"><input type="checkbox" required disabled={!phoneMatch || phoneCheck.isPending || grant.isPending} checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>我已核對手機持有人身分及線下入社／繳費資料。</span></label>
      {grant.isError && <p className="form-error" role="alert">{grant.error.message}</p>}
      <button className="button button-primary" disabled={grant.isPending || phoneCheck.isPending || !phoneMatch || !confirmed}>{grant.isPending ? "確認中…" : "確認授予正式社員資格"}</button>
    </form>}
    {grant.isSuccess && <p role="status">社員資格已啟用。</p>}
  </article>;
}
