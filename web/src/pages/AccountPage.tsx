import {
  Bell,
  Certificate,
  Coins,
  CreditCard,
  Receipt,
  SignOut,
  Sparkle,
  UserCircle,
} from "@phosphor-icons/react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import { createMembershipPayment } from "../lib/commerce";
import type {
  MemberBadge,
  MembershipCharge,
  MembershipSummary,
  Notification,
  PointSummary,
  SurplusDistribution,
} from "../lib/types";

const membershipLabels: Record<string, string> = {
  member: "正式社員",
  trainee: "準社員",
  nonmember: "一般消費者",
};

const chargeLabels: Record<string, string> = {
  admission_fee: "入社費",
  annual_fee: "年費",
  share_capital: "股金",
};

export function AccountPage() {
  const { user, openLogin, logout } = useAuth();
  const search = useSearch({ from: "/account" });
  const queryClient = useQueryClient();
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const isActiveMember = membership.data?.membership_type === "member";
  const [charges, notifications, points, badges, surplus] = useQueries({
    queries: [
      {
        queryKey: ["membership-charges", user?.id],
        queryFn: () => apiFetch<MembershipCharge[]>("/v1/membership/charges"),
        enabled: Boolean(user),
      },
      {
        queryKey: ["notifications", user?.id],
        queryFn: () => apiFetch<Notification[]>("/v1/notifications?limit=12"),
        enabled: Boolean(user),
      },
      {
        queryKey: ["my-points", user?.id],
        queryFn: () => apiFetch<PointSummary>("/v1/me/points"),
        enabled: Boolean(user && isActiveMember),
      },
      {
        queryKey: ["my-badges", user?.id],
        queryFn: () => apiFetch<MemberBadge[]>("/v1/me/badges"),
        enabled: Boolean(user && isActiveMember),
      },
      {
        queryKey: ["my-surplus", user?.id],
        queryFn: () => apiFetch<SurplusDistribution[]>("/v1/me/surplus-distributions"),
        enabled: Boolean(user && isActiveMember),
      },
    ],
  });
  const payCharge = useMutation({
    mutationFn: createMembershipPayment,
    onSuccess: (url) => window.location.assign(url),
  });
  const markAllRead = useMutation({
    mutationFn: () => apiFetch("/v1/notifications/read-all", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  if (!user) {
    return (
      <section className="account-gate">
        <UserCircle size={48} weight="light" />
        <p className="eyebrow">MEMBER CENTER</p>
        <h1>你的合作生活，集中在這裡。</h1>
        <p>登入後查看社員資格、應繳款、點數、徽章、盈餘分配與通知。</p>
        <button className="button button-primary" type="button" onClick={openLogin}>帳號登入</button>
      </section>
    );
  }

  const unreadCount = notifications.data?.filter((item) => !item.read_at).length || 0;

  return (
    <section className="account-page">
      <header className="workspace-heading account-heading">
        <div>
          <p className="eyebrow">MEMBER CENTER / 會員中心</p>
          <h1>{user.display_name}，<br />這是你的合作紀錄。</h1>
        </div>
        <div className="account-identity">
          <span>{membershipLabels[membership.data?.membership_type || "nonmember"]}</span>
          <strong>{membership.data?.membership?.member_number || membership.data?.membership?.trainee_number || "尚無社員編號"}</strong>
          <small>{user.email}</small>
          <button type="button" onClick={logout}><SignOut size={16} />登出</button>
        </div>
      </header>

      <nav className="account-shortcuts" aria-label="會員快捷功能">
        <Link to="/orders" search={{ order_id: undefined, result: undefined, message: undefined }}><Receipt size={19} />我的訂單</Link>
        <Link to="/social"><Certificate size={19} />社務系統</Link>
        {user.user_role === "admin" && <Link to="/admin"><Sparkle size={19} />管理工作台</Link>}
      </nav>

      {search.payment && (
        <div className={`return-banner ${search.payment}`}>
          <CreditCard size={22} />
          <div>
            <strong>{search.payment === "confirming" ? "款項確認中" : "綠界款項已返回"}</strong>
            <span>系統正在同步社員款項狀態，稍後重新整理即可看到結果。</span>
          </div>
        </div>
      )}

      {membership.isPending && <LoadingLines count={2} />}
      <section className="account-metrics">
        <Metric icon={Coins} label="合作點數" value={isActiveMember ? String(points.data?.balance ?? "—") : "未開放"} />
        <Metric icon={Certificate} label="已獲徽章" value={isActiveMember ? String(badges.data?.length ?? "—") : "—"} />
        <Metric icon={CreditCard} label="待繳款項" value={String(charges.data?.filter((charge) => charge.status === "pending").length ?? "—")} />
        <Metric icon={Bell} label="未讀通知" value={String(unreadCount)} />
      </section>

      <div className="account-grid">
        <section className="account-panel charges-panel">
          <div className="panel-heading-inline">
            <div><p className="eyebrow">MEMBERSHIP PAYMENTS</p><h2>社員款項</h2></div>
            <CreditCard size={27} weight="light" />
          </div>
          {charges.isPending && <LoadingLines count={3} />}
          {charges.isError && <DataState kind="error" title="款項資料無法讀取" detail={charges.error.message} />}
          {charges.data?.length === 0 && <DataState title="目前沒有社員款項" detail="需要繳納的入社費、年費或股金會列在這裡。" />}
          <div className="charge-list">
            {charges.data?.map((charge) => (
              <article key={charge.id}>
                <div>
                  <small>{chargeLabels[charge.charge_kind] || charge.charge_kind}</small>
                  <strong>{formatMoney(charge.amount)}</strong>
                </div>
                <span className={`order-state ${charge.status}`}>{chargeStatusLabel(charge.status)}</span>
                {charge.status === "pending" && (
                  <button
                    className="button button-system"
                    type="button"
                    onClick={() => payCharge.mutate(charge.id)}
                    disabled={payCharge.isPending}
                  >
                    綠界繳款
                  </button>
                )}
                {charge.status === "paid" && <small>收據 {charge.receipt_number || "產生中"}</small>}
              </article>
            ))}
          </div>
          {payCharge.isError && <p className="form-error">{payCharge.error.message}</p>}
        </section>

        <section className="account-panel notification-panel">
          <div className="panel-heading-inline">
            <div><p className="eyebrow">NOTIFICATIONS</p><h2>最新通知</h2></div>
            {unreadCount > 0 && (
              <button type="button" onClick={() => markAllRead.mutate()} disabled={markAllRead.isPending}>全部已讀</button>
            )}
          </div>
          {notifications.isPending && <LoadingLines count={4} />}
          {notifications.data?.length === 0 && <DataState title="目前沒有通知" detail="訂單、社務與款項進度會在這裡提醒你。" />}
          <div className="notification-list">
            {notifications.data?.map((notification) => (
              <article key={notification.id} className={notification.read_at ? "read" : ""}>
                <i />
                <div><strong>{notification.title}</strong><p>{notification.body}</p></div>
                <time>{formatDate(notification.created_at)}</time>
              </article>
            ))}
          </div>
        </section>

        <section className="account-panel cooperative-panel">
          <div className="panel-heading-inline">
            <div><p className="eyebrow">CO-OP RECORD</p><h2>合作成果</h2></div>
            <Sparkle size={27} weight="light" />
          </div>
          {!isActiveMember ? (
            <DataState title="正式社員功能尚未開放" detail="點數、徽章與盈餘分配只顯示已確認的合作社資料。" />
          ) : (
            <>
              <div className="badge-list">
                {badges.data?.map((badge) => (
                  <article key={badge.key}><Certificate size={23} /><div><strong>{badge.name}</strong><small>{badge.description}</small></div></article>
                ))}
                {badges.data?.length === 0 && <p>參與消費、表決與共同購買後，達成的合作徽章會出現在這裡。</p>}
              </div>
              <div className="surplus-list">
                {surplus.data?.map((item) => (
                  <div key={item.fiscal_year_id}><span>{item.label}</span><strong>{formatMoney(item.distribution_amount)}</strong><small>貢獻額 {formatMoney(item.contribution_amount)}</small></div>
                ))}
              </div>
            </>
          )}
        </section>
      </div>
    </section>
  );
}

function Metric({ icon: Icon, label, value }: { icon: typeof Coins; label: string; value: string }) {
  return <article><Icon size={22} weight="light" /><span>{label}</span><strong>{value}</strong></article>;
}

function chargeStatusLabel(status: string): string {
  return { pending: "待繳", paid: "已繳", refunded: "已退款", cancelled: "已取消" }[status] || status;
}
