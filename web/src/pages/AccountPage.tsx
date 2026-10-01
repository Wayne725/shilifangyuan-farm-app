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
import { type FormEvent, useEffect, useRef, useState } from "react";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { openMembershipHandoff } from "../lib/checkout-navigation";
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import { createMembershipPayment, refreshPaymentAttempt } from "../lib/commerce";
import type {
  MemberBadge,
  MembershipCharge,
  MembershipSummary,
  Notification,
  PaymentAttemptStatus,
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
  const { user, isAuthReady, openLogin, logout } = useAuth();
  const search = useSearch({ from: "/account" });
  const queryClient = useQueryClient();
  const confirmationStartedAt = useRef(Date.now());
  const paymentConfirmation = useQuery({
    queryKey: ["payment-attempt-refresh", search.attempt_id],
    queryFn: () => refreshPaymentAttempt(search.attempt_id || ""),
    enabled: Boolean(
      user && search.payment === "confirming" && search.attempt_id,
    ),
    refetchInterval: (query) => {
      const status = (query.state.data as PaymentAttemptStatus | undefined)
        ?.status;
      const elapsed = Date.now() - confirmationStartedAt.current;
      if (status && status !== "pending" && status !== "confirming") return false;
      if (elapsed >= 120_000) return false;
      return elapsed < 30_000 ? 5000 : 15_000;
    },
    refetchIntervalInBackground: false,
  });
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
        refetchInterval: search.payment === "confirming" ? 15_000 : false,
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
    onSuccess: (url, chargeId) => openMembershipHandoff(url, chargeId),
  });
  const markAllRead = useMutation({
    mutationFn: () => apiFetch("/v1/notifications/read-all", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const refreshedPaymentStatus = paymentConfirmation.data?.status;
  const storedChargeStatus = charges.data?.find(
    (charge) => charge.id === search.membership_charge_id,
  )?.status;
  const displayedPaymentStatus =
    refreshedPaymentStatus
      && !["pending", "confirming"].includes(refreshedPaymentStatus)
      ? refreshedPaymentStatus
      : storedChargeStatus && storedChargeStatus !== "pending"
        ? storedChargeStatus
        : search.payment;

  useEffect(() => {
    confirmationStartedAt.current = Date.now();
  }, [search.attempt_id]);

  useEffect(() => {
    if (
      refreshedPaymentStatus
      && !["pending", "confirming"].includes(refreshedPaymentStatus)
    ) {
      queryClient.invalidateQueries({ queryKey: ["membership-charges"] });
      queryClient.invalidateQueries({ queryKey: ["membership-me"] });
    }
  }, [queryClient, refreshedPaymentStatus]);

  if (!isAuthReady) {
    return (
      <section className="account-gate" aria-live="polite">
        <LoadingLines count={3} />
        <p>正在恢復登入狀態與付款結果…</p>
      </section>
    );
  }

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

      {displayedPaymentStatus && (
        <div className={`return-banner ${displayedPaymentStatus}`}>
          <CreditCard size={22} />
          <div>
            <strong>{membershipPaymentTitle(displayedPaymentStatus)}</strong>
            <span>{membershipPaymentMessage(displayedPaymentStatus)}</span>
          </div>
        </div>
      )}

      {membership.isPending && <LoadingLines count={2} />}
      {membership.data?.membership_type === "nonmember" && (
        <ExistingMemberClaimPanel
          onDone={() => queryClient.invalidateQueries({ queryKey: ["membership-me", user.id] })}
        />
      )}
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
                {charge.status === "pending" && !(
                  displayedPaymentStatus === "confirming"
                  && charge.id === search.membership_charge_id
                ) && (
                  <button
                    className="button button-system"
                    type="button"
                    onClick={() => payCharge.mutate(charge.id)}
                    disabled={payCharge.isPending}
                  >
                    線上繳款
                  </button>
                )}
                {charge.status === "pending"
                  && displayedPaymentStatus === "confirming"
                  && charge.id === search.membership_charge_id
                  && <small>正在確認款項，請勿重複付款</small>}
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

function membershipPaymentTitle(status: string): string {
  if (status === "paid") return "款項已確認";
  if (status === "confirming" || status === "pending") return "款項確認中";
  if (status === "expired") return "付款時間已結束";
  return "線上付款狀態已更新";
}

function membershipPaymentMessage(status: string): string {
  if (status === "paid") return "款項已由金流查詢確認。";
  if (status === "confirming" || status === "pending") {
    return "系統正在向金流查詢，請勿重複付款；離開頁面後仍會在背景補查。";
  }
  if (status === "expired") return "若已扣款，系統仍會繼續補查。";
  return "請查看下方社員款項狀態。";
}

function ExistingMemberClaimPanel({ onDone }: { onDone: () => void }) {
  const [memberNumber, setMemberNumber] = useState("");
  const [legalName, setLegalName] = useState("");
  const [phone, setPhone] = useState("");
  const claim = useMutation({
    mutationFn: () => apiFetch("/v1/membership/claim-existing", {
      method: "POST",
      body: JSON.stringify({
        member_number: memberNumber.trim(),
        legal_name: legalName.trim(),
        phone: phone.trim(),
      }),
    }),
    onSuccess: onDone,
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    claim.mutate();
  }

  return (
    <form className="social-form existing-member-claim" onSubmit={submit}>
      <div className="form-heading">
        <div><p className="eyebrow">EXISTING MEMBER CLAIM</p><h2>已是社員？認領既有資格</h2></div>
        <span>系統會以此帳號 Email 核對合作社名冊，不會重新走入社流程。</span>
      </div>
      <div className="field-grid three-columns">
        <label className="field"><span>社員編號</span><input required maxLength={32} value={memberNumber} onChange={(event) => setMemberNumber(event.target.value)} /></label>
        <label className="field"><span>名冊登記姓名</span><input required maxLength={80} value={legalName} onChange={(event) => setLegalName(event.target.value)} /></label>
        <label className="field"><span>名冊登記手機</span><input required type="tel" minLength={8} maxLength={24} value={phone} onChange={(event) => setPhone(event.target.value)} /></label>
      </div>
      {claim.isError && <p className="form-error" role="alert">{claim.error.message}</p>}
      <div className="form-actions">
        <Link className="button button-quiet" to="/membership">不是既有社員，提出入社申請</Link>
        <button className="button button-primary" disabled={claim.isPending}>{claim.isPending ? "核對中…" : "認領社員資格"}</button>
      </div>
    </form>
  );
}

function Metric({ icon: Icon, label, value }: { icon: typeof Coins; label: string; value: string }) {
  return <article><Icon size={22} weight="light" /><span>{label}</span><strong>{value}</strong></article>;
}

function chargeStatusLabel(status: string): string {
  return { pending: "待繳", paid: "已繳", refunded: "已退款", cancelled: "已取消" }[status] || status;
}
