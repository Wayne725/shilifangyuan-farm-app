import { CheckCircle, IdentificationCard, UsersThree } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { AdminReviewSection } from "./AdminReviewSection";
import { OptionalMark } from "./OptionalMark";
import { RosterCsvImport } from "./RosterCsvImport";
import { SocialActionButton } from "./SocialActionButton";
import { ApiError, apiFetch, formatDateTime } from "../lib/api";

interface ShareholdingRecord {
  id: string;
  share_capital_amount: number;
  share_count: number;
  updated_at: string;
}

export interface AdminMembership extends ShareholdingRecord {
  user_id: string;
  display_name?: string;
  status: string;
  member_number?: string | null;
  trainee_number?: string | null;
}


interface MemberRosterEntry extends ShareholdingRecord {
  member_number: string;
  legal_name: string;
  email_masked: string;
  phone_masked: string;
  is_active: boolean;
  claimed: boolean;
  claimed_at?: string | null;
}

interface PanelProps {
  adminId: string;
  request: typeof apiFetch;
  member?: string;
}

function useMemberRoster({ adminId, request }: PanelProps, enabled = true) {
  return useQuery({ queryKey: ["admin-member-roster", adminId], queryFn: ({ signal }) => request<MemberRosterEntry[]>("/v1/admin/member-roster", { signal }), enabled });
}

export function useAdminMemberships({ adminId, request }: PanelProps, enabled: boolean) {
  return useQuery({ queryKey: ["admin-members", adminId], queryFn: ({ signal }) => request<AdminMembership[]>("/v1/admin/members", { signal }), enabled });
}

export function AdminMembershipPanel({ adminId, request, memberships, membership, member, q }: PanelProps & {
  memberships: ReturnType<typeof useAdminMemberships>;
  membership?: string;
  q?: string;
}) {
  const client = useQueryClient();
  const [search, setSearch] = useState(q || "");
  const [status, setStatus] = useState("");
  const roster = useMemberRoster({ adminId, request }, !membership || Boolean(member));
  const names = new Map(roster.data?.map(entry => [entry.member_number, entry.legal_name]));
  const term = search.trim().toLowerCase();
  const visibleMembers = (memberships.data || []).filter(item =>
    (!membership || item.id === membership) && (!member || item.member_number === member) && (!status || item.status === status)
  ).map(item => ({ ...item, display_name: names.get(item.member_number || "") ?? item.display_name })).filter(item =>
    `${item.display_name || ""} ${item.member_number || ""} ${item.trainee_number || ""} ${item.id} ${item.user_id}`.toLowerCase().includes(term)
  );
  const refresh = () => {
    for (const key of ["admin-members", "admin-registered-users", "member-register", "points-ledger"]) {
      void client.invalidateQueries({ queryKey: [key] });
    }
  };
  return <>
    {membership || member ? <div className="admin-quick-buttons">
      {member && <span>社員編號 {member}</span>}
      <Link className="text-link" to="/admin/users" search={{ tab: "members" }}>查看全部會籍</Link>
    </div> : <div className="social-member-filters">
      <label className="field"><span>姓名、社員編號或會員識別碼</span><input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label>
      <label className="field"><span>會籍狀態</span><select value={status} onChange={event => setStatus(event.target.value)}>
        <option value="">全部狀態</option>
        {["trainee", "active", "suspended", "resigned", "terminated"].map(value => <option key={value} value={value}>{membershipStatusLabel(value)}</option>)}
      </select></label>
    </div>}
    <AdminReviewSection icon={UsersThree} title="會籍管理" count={visibleMembers.length} pending={memberships.isPending} error={memberships.error?.message} empty="沒有符合條件的會籍">
      {visibleMembers.map(item => <MembershipStatusCard key={item.id} membership={item} adminId={adminId} request={request} onDone={refresh} />)}
    </AdminReviewSection>
  </>;
}

export function AdminRosterPanel({ adminId, request, member }: PanelProps) {
  const client = useQueryClient();
  const roster = useMemberRoster({ adminId, request });
  const entries = (roster.data || []).filter(entry => !member || entry.member_number === member);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["admin-member-roster"] });
    void client.invalidateQueries({ queryKey: ["member-register"] });
  };
  return <>
    {member ? <div className="admin-quick-buttons"><span>社員編號 {member}</span><Link className="text-link" to="/admin/users" search={{ tab: "roster" }}>查看全部名冊</Link></div> : <RosterCsvImport request={request} onDone={refresh} />}
    <AdminReviewSection icon={IdentificationCard} title="既有社員名冊" count={entries.length} pending={roster.isPending} error={roster.error?.message} empty={member ? "沒有符合條件的名冊資料" : "尚未匯入既有社員"}>
      {entries.map(entry => <RosterEntryCard key={entry.id} entry={entry} adminId={adminId} request={request} />)}
    </AdminReviewSection>
  </>;
}

function RosterEntryCard({ entry, adminId, request }: PanelProps & { entry: MemberRosterEntry }) {
  return (
    <article className="admin-review-card roster-entry-card">
      <div className="review-card-copy">
        <span className="status-chip">{entry.claimed ? "已認領" : entry.is_active ? "可認領" : "已停用"}</span>
        <h3>{entry.member_number} · {entry.legal_name}</h3>
        <p>{entry.email_masked} · {entry.phone_masked || "未提供電話"}</p>
        <small>股金 {entry.share_capital_amount} 元 · {entry.share_count} 股</small>
      </div>
      <div className="review-card-actions"><strong>{entry.claimed ? "已連結社員帳號" : "等待社員完成名冊核對"}</strong>{entry.claimed_at && <small>{formatDateTime(entry.claimed_at)}</small>}<ShareholdingEditor record={entry} source="roster" adminId={adminId} request={request} /></div>
    </article>
  );
}

function ShareholdingEditor({ record, source, adminId, request }: PanelProps & { record: ShareholdingRecord; source: "roster" | "membership" }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(String(record.share_capital_amount));
  const [count, setCount] = useState(String(record.share_count));
  const [reason, setReason] = useState("");
  const [expectedUpdatedAt, setExpectedUpdatedAt] = useState(record.updated_at);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<ShareholdingRecord | null>(null);
  const [confirmedLatest, setConfirmedLatest] = useState(false);
  const [saved, setSaved] = useState(false);
  const endpoint = source === "roster" ? `/v1/admin/member-roster/${record.id}/shares` : `/v1/admin/members/${record.id}/shares`;
  const validInteger = (value: string) => value.trim() !== "" && Number.isInteger(Number(value)) && Number(value) >= 0 && Number(value) <= 2147483647;
  const valid = validInteger(amount) && validInteger(count) && reason.trim().length <= 1000;
  const versionReady = conflict ? Boolean(latest?.updated_at && confirmedLatest) : Boolean(expectedUpdatedAt);
  const save = useMutation({
    mutationFn: () => {
      if (!valid || !versionReady) throw new Error("請填寫非負整數，並核對最新股籍版本");
      return request<ShareholdingRecord>(endpoint, {
        method: "PATCH",
        body: JSON.stringify({
          share_capital_amount: Number(amount),
          share_count: Number(count),
          reason: reason.trim(),
          expected_updated_at: conflict ? latest?.updated_at : expectedUpdatedAt,
        }),
      });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin-member-roster"] }),
        queryClient.invalidateQueries({ queryKey: ["admin-members"] }),
        queryClient.invalidateQueries({ queryKey: ["member-register"] }),
      ]);
      setOpen(false);
      setSaved(true);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setLatest(null);
        setConfirmedLatest(false);
      }
    },
  });
  const reload = useMutation({
    mutationFn: async () => {
      const [roster, members] = await Promise.all([
        queryClient.fetchQuery({ queryKey: ["admin-member-roster", adminId], queryFn: ({ signal }) => request<MemberRosterEntry[]>("/v1/admin/member-roster", { signal }), staleTime: 0 }),
        queryClient.fetchQuery({ queryKey: ["admin-members", adminId], queryFn: ({ signal }) => request<AdminMembership[]>("/v1/admin/members", { signal }), staleTime: 0 }),
      ]);
      const refreshed = (source === "roster" ? roster : members).find((item) => item.id === record.id);
      if (!refreshed?.updated_at) throw new Error("找不到可核對的最新股籍版本，請稍後重讀或聯絡系統管理者");
      return refreshed;
    },
    onSuccess: (refreshed) => { setLatest(refreshed); setConfirmedLatest(false); },
  });
  const begin = () => {
    setAmount(String(record.share_capital_amount)); setCount(String(record.share_count));
    setReason(""); setExpectedUpdatedAt(record.updated_at); setConflict(false); setLatest(null);
    setConfirmedLatest(false); setSaved(false); save.reset(); reload.reset(); setOpen(true);
  };
  return <div>
    {!open && <button type="button" disabled={!record.updated_at} onClick={begin}>編輯股金／股數</button>}
    {!record.updated_at && <p className="field-help">暫無股籍版本資訊，請重新讀取名冊後再編輯。</p>}
    {saved && <p className="form-success" role="status">股籍已更新；未收款或退款。</p>}
    {open && <form aria-label="修改股金與股數" onSubmit={(event) => { event.preventDefault(); if (valid && versionReady) save.mutate(); }}>
      <p role="note">僅修正股籍，不會收款或退款。已認領社員的名冊與會籍會同步更新，社員端不顯示股金與股數。</p>
      <label className="field"><span>股金（元）</span><input required type="number" min={0} max={2147483647} step={1} disabled={save.isPending} value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
      <label className="field"><span>股數</span><input required type="number" min={0} max={2147483647} step={1} disabled={save.isPending} value={count} onChange={(event) => setCount(event.target.value)} /></label>
      <label className="field"><span>修改原因<OptionalMark /></span><textarea aria-label="修改原因" maxLength={1000} rows={3} disabled={save.isPending} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      {(!validInteger(amount) || !validInteger(count)) && <p className="form-error">股金與股數須為 0 至 2147483647 的整數。</p>}
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
      {conflict && <div>
        <p className="form-error">股籍資料已變更或連結需核對。你的輸入已保留，請先重新讀取，確認最新資料後再決定是否送出。</p>
        <button type="button" disabled={reload.isPending || save.isPending} onClick={() => { setLatest(null); setConfirmedLatest(false); reload.mutate(); }}>{reload.isPending ? "重新讀取中…" : "重新讀取最新股籍"}</button>
        {latest && <><p>最新股金 {latest.share_capital_amount} 元 · {latest.share_count} 股</p><label className="meal-choice"><input type="checkbox" checked={confirmedLatest} disabled={save.isPending} onChange={(event) => setConfirmedLatest(event.target.checked)} /><span>我已核對最新股籍，確認以目前輸入修正</span></label></>}
        {reload.isError && <p className="form-error" role="alert">{reload.error.message}</p>}
      </div>}
      <div className="form-actions"><button type="button" disabled={save.isPending || reload.isPending} onClick={() => setOpen(false)}>取消編輯</button><button type="submit" disabled={!valid || !versionReady || save.isPending || reload.isPending}>{save.isPending ? "儲存中…" : "儲存股籍修正"}</button></div>
    </form>}
  </div>;
}

export function MembershipStatusCard({ membership, onDone, adminId, request = apiFetch }: {
  membership: AdminMembership;
  onDone: () => void;
  adminId: string;
  request?: typeof apiFetch;
}) {
  const action = useMutation({
    mutationFn: ({nextAction, reason}: {nextAction: "activate" | "suspend" | "resign" | "terminate" | "share-capital-return"; reason: string}) => request(`/v1/admin/members/${membership.id}/${nextAction}`, { method: "POST", body: JSON.stringify({ reason }) }),
    onSuccess: onDone,
  });
  return <article className="admin-review-card"><div className="review-card-copy"><span className="status-chip">{membershipStatusLabel(membership.status)}</span><h3>{membership.display_name && `${membership.display_name} · `}{membership.member_number || membership.trainee_number || "待編號"}</h3><p>股金 {membership.share_capital_amount} 元 · {membership.share_count} 股</p><details><summary>會籍詳細資料</summary><small>會籍 ID {membership.id}</small></details></div><div className="review-card-actions"><div>{membership.status === "trainee" && <SocialActionButton disabled={action.isPending} action="activate" showReason={true} onConfirm={reason => action.mutate({nextAction: "activate", reason})}><CheckCircle size={16} />轉為正式社員</SocialActionButton>}{membership.status === "active" && <SocialActionButton disabled={action.isPending} action="suspend" showReason={true} onConfirm={reason => action.mutate({nextAction: "suspend", reason})}>暫停會籍</SocialActionButton>}{["active", "suspended"].includes(membership.status) && <SocialActionButton disabled={action.isPending} action="resign" showReason={true} onConfirm={reason => action.mutate({nextAction: "resign", reason})}>辦理退社</SocialActionButton>}{["trainee", "active", "suspended", "resigned"].includes(membership.status) && <SocialActionButton disabled={action.isPending} action="terminate" showReason={true} onConfirm={reason => action.mutate({nextAction: "terminate", reason})}>終止會籍</SocialActionButton>}{membership.status === "resigned" && <SocialActionButton disabled={action.isPending} action="share-capital-return" showReason={true} onConfirm={reason => action.mutate({nextAction: "share-capital-return", reason})}>返還股金</SocialActionButton>}</div>{action.isSuccess && <p role="status">處理完成</p>}{action.isError && <p className="form-error" role="alert">{action.error.message}</p>}<ShareholdingEditor record={membership} source="membership" adminId={adminId} request={request} /></div></article>;
}

function membershipStatusLabel(status: string) {
  return { pending_payment: "待繳款", trainee: "實習社員", active: "正式社員", suspended: "暫停", resigned: "已退社", terminated: "已終止" }[status] || status;
}
