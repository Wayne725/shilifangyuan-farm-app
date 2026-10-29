import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch } from "../lib/api";
import type { MembershipApplication } from "../lib/types";

const statusLabels: Record<string, string> = {
  draft: "草稿", submitted: "待審核", needs_supplement: "待補件",
  approved: "已核准", rejected: "已駁回", withdrawn: "已撤回",
};

export function MembershipApplicationPage() {
  const { user, openLogin } = useAuth();
  const application = useQuery({
    queryKey: ["membership-application", user?.id],
    queryFn: async () => {
      try {
        return await apiFetch<MembershipApplication>("/v1/membership/application");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(user),
  });
  if (!user) return <section className="account-gate"><h1>舊版入社資料</h1><button className="button button-primary" onClick={openLogin}>登入帳號</button></section>;
  const profile = application.data?.profile;
  return <section className="membership-page">
    <Link className="text-link" to="/membership">返回入社申請</Link>
    <header className="workspace-heading"><div><p className="eyebrow">ADMISSION HISTORY / 歷史資料</p><h1>舊版入社資料</h1></div></header>
    {application.isPending ? <LoadingLines count={4} /> : application.isError ? <DataState title="舊版資料無法讀取" detail={application.error.message} /> : !application.data ? <DataState title="沒有舊版入社紀錄" detail="" /> : <>
      <p className="status-chip">{statusLabels[application.data.status] || application.data.status}</p>
      {application.data.review_reason && <p className="return-banner">{application.data.review_reason}</p>}
      {profile && <dl className="detail-grid">
        <div><dt>姓名</dt><dd>{profile.legal_name}</dd></div>
        <div><dt>出生日期</dt><dd>{profile.birth_date}</dd></div>
        <div><dt>手機</dt><dd>{profile.phone}</dd></div>
        <div><dt>現居地址</dt><dd>{profile.address}</dd></div>
        <div><dt>緊急聯絡人與電話</dt><dd>{profile.emergency_contact}</dd></div>
      </dl>}
      <Link className="button button-quiet" to="/account" search={{ membership_charge_id: undefined, payment: undefined }}>查看社員中心與歷史款項</Link>
    </>}
  </section>;
}
