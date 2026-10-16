import { CheckCircle, IdentificationCard, UserCircle } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";

import { LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch } from "../lib/api";
import type { MembershipApplication, MembershipSummary } from "../lib/types";

const emptyForm = {
  legal_name: "",
  phone: "",
  birth_date: "",
  address: "",
  emergency_contact: "",
  gender: "",
  place_of_origin: "",
  occupation: "",
  registered_address: "",
  correspondence_address: "",
  landline_phone: "",
  line_id: "",
};

const genderOptions = ["男", "女", "非二元", "不透露"];
const placeOfOriginOptions = [
  "台北市", "新北市", "桃園市", "台中市", "台南市", "高雄市", "基隆市", "新竹市", "嘉義市",
  "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
  "台東縣", "澎湖縣", "金門縣", "連江縣",
];
const occupationOptions = [
  "農林漁牧", "製造／營造", "商業／金融", "教育／研究", "醫療／照護", "資訊／科技", "餐飲／服務",
  "公務／行政", "自由業", "學生", "家管／照護", "退休", "待業",
];

export function MembershipApplicationPage() {
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [genderChoice, setGenderChoice] = useState("");
  const [placeOfOriginChoice, setPlaceOfOriginChoice] = useState("");
  const [occupationChoice, setOccupationChoice] = useState("");
  const [consented, setConsented] = useState(false);
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
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

  useEffect(() => {
    const profile = application.data?.profile;
    if (!profile) return;
    setForm({
      legal_name: profile.legal_name,
      phone: profile.phone,
      birth_date: profile.birth_date,
      address: profile.address,
      emergency_contact: profile.emergency_contact,
      gender: profile.gender || "",
      place_of_origin: profile.place_of_origin || "",
      occupation: profile.occupation || "",
      registered_address: profile.registered_address || "",
      correspondence_address: profile.correspondence_address || "",
      landline_phone: profile.landline_phone || "",
      line_id: profile.line_id || "",
    });
    setGenderChoice(genderOptions.includes(profile.gender || "") ? profile.gender || "" : profile.gender ? "__other" : "");
    setPlaceOfOriginChoice(placeOfOriginOptions.includes(profile.place_of_origin || "") ? profile.place_of_origin || "" : profile.place_of_origin ? "__other" : "");
    setOccupationChoice(occupationOptions.includes(profile.occupation || "") ? profile.occupation || "" : profile.occupation ? "__other" : "");
    setConsented(true);
  }, [application.data?.profile]);

  const saveProfile = useMutation({
    mutationFn: () => apiFetch<MembershipApplication>("/v1/membership/application", {
      method: "PUT",
      body: JSON.stringify({
        ...form,
        consent_version: application.data?.profile?.consent_version || "membership-data-v1",
        gender: form.gender || null,
        place_of_origin: form.place_of_origin || null,
        occupation: form.occupation || null,
        registered_address: form.registered_address || null,
        correspondence_address: form.correspondence_address || null,
        landline_phone: form.landline_phone || null,
        line_id: form.line_id || null,
      }),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["membership-application"] });
      queryClient.invalidateQueries({ queryKey: ["membership-me"] });
    },
  });
  const submitApplication = useMutation({
    mutationFn: () => apiFetch<MembershipApplication>(`/v1/membership/application/${application.data?.status === "needs_supplement" ? "supplement" : "submit"}`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["membership-application"] });
      queryClient.invalidateQueries({ queryKey: ["membership-charges"] });
    },
  });
  const withdrawApplication = useMutation({
    mutationFn: () => apiFetch<MembershipApplication>("/v1/membership/application/withdraw", { method: "POST", body: JSON.stringify({ reason: "申請人自行撤回" }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["membership-application"] }),
  });

  function save(event: FormEvent) {
    event.preventDefault();
    saveProfile.mutate();
  }

  if (!user) {
    return <section className="account-gate"><UserCircle size={48} weight="light" /><p className="eyebrow">MEMBERSHIP / 社員資格</p><h1>開始入社申請</h1><p>登入後填寫資料並查看申請進度。</p><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></section>;
  }
  if (membership.isPending || application.isPending) return <section className="membership-page"><LoadingLines count={5} /></section>;
  if (membership.data?.membership_type === "member") {
    return <section className="membership-page"><header className="membership-heading"><CheckCircle size={42} weight="light" /><p className="eyebrow">ACTIVE MEMBER / 正式社員</p><h1>你已是正式社員</h1><p>{membership.data.membership?.member_number || "社員資格已啟用"}</p><div><Link className="button button-primary" to="/social">進入社務系統</Link><Link className="button button-quiet" to="/account" search={{ membership_charge_id: undefined, payment: undefined }}>查看社員中心</Link></div></header></section>;
  }

  const previousStatus = application.data?.status || "draft";
  const reapplying = membership.data?.membership?.status === "suspended" && ["approved", "rejected", "withdrawn"].includes(previousStatus);
  const status = reapplying ? "draft" : previousStatus;
  const editable = ["draft", "needs_supplement"].includes(status);

  return (
    <section className="membership-page">
      <header className="workspace-heading membership-workspace-heading">
        <div><p className="eyebrow">MEMBERSHIP APPLICATION / 入社申請</p><h1>入社申請</h1></div>
        <div className="application-status"><span>{reapplying ? "重新申請" : applicationStatusLabel(status)}</span></div>
      </header>

      {application.data?.review_reason && <div className="return-banner failed"><IdentificationCard size={22} /><div><strong>審核意見</strong><span>{application.data.review_reason}</span></div></div>}

      <form className="membership-form" onSubmit={save}>
        <section>
          <div className="form-heading"><div><p className="eyebrow">MASTER DATA / 基本資料</p><h2>基本資料</h2></div></div>
          <div className="field-grid three-columns">
            <label className="field"><span>姓名</span><input required disabled={!editable} value={form.legal_name} onChange={(event) => setForm({ ...form, legal_name: event.target.value })} /></label>
            <label className="field"><span>出生日期</span><input required disabled={!editable} type="date" value={form.birth_date} onChange={(event) => setForm({ ...form, birth_date: event.target.value })} /></label>
            <label className="field"><span>性別</span><select disabled={!editable} value={genderChoice} onChange={(event) => { const value = event.target.value; setGenderChoice(value); setForm({ ...form, gender: value === "__other" ? "其他" : value }); }}><option value="">請選擇</option>{genderOptions.map((option) => <option key={option} value={option}>{option}</option>)}<option value="__other">其他</option></select></label>
            <label className="field"><span>籍貫</span><select disabled={!editable} value={placeOfOriginChoice} onChange={(event) => { const value = event.target.value; setPlaceOfOriginChoice(value); setForm({ ...form, place_of_origin: value === "__other" ? "其他／國外" : value }); }}><option value="">請選擇</option>{placeOfOriginOptions.map((option) => <option key={option} value={option}>{option}</option>)}<option value="__other">其他／國外</option></select></label>
            <label className="field"><span>職業</span><select disabled={!editable} value={occupationChoice} onChange={(event) => { const value = event.target.value; setOccupationChoice(value); setForm({ ...form, occupation: value === "__other" ? "其他" : value }); }}><option value="">請選擇</option>{occupationOptions.map((option) => <option key={option} value={option}>{option}</option>)}<option value="__other">其他</option></select></label>
            <label className="field"><span>手機</span><input required disabled={!editable} value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} /></label>
            <label className="field"><span>市話</span><input disabled={!editable} value={form.landline_phone} onChange={(event) => setForm({ ...form, landline_phone: event.target.value })} /></label>
            <label className="field"><span>LINE ID</span><input disabled={!editable} value={form.line_id} onChange={(event) => setForm({ ...form, line_id: event.target.value })} /></label>
            <label className="field wide"><span>現居地址</span><input required disabled={!editable} value={form.address} onChange={(event) => setForm({ ...form, address: event.target.value })} /></label>
            <label className="field wide"><span>戶籍地址</span><input disabled={!editable} value={form.registered_address} onChange={(event) => setForm({ ...form, registered_address: event.target.value })} /></label>
            <label className="field wide"><span>通訊地址</span><input disabled={!editable} value={form.correspondence_address} onChange={(event) => setForm({ ...form, correspondence_address: event.target.value })} /></label>
            <label className="field wide"><span>緊急聯絡人與電話</span><input required disabled={!editable} value={form.emergency_contact} onChange={(event) => setForm({ ...form, emergency_contact: event.target.value })} /></label>
          </div>
          {editable && <label className="check-field consent-field"><input required type="checkbox" checked={consented} onChange={(event) => setConsented(event.target.checked)} /><span>我確認資料正確，並同意合作社為入社審核與社員管理使用上述資料。</span></label>}
          {saveProfile.isError && <p className="form-error">{saveProfile.error.message}</p>}
          {saveProfile.isSuccess && <p className="form-success">基本資料已儲存。</p>}
          {editable && <div className="form-actions"><button className="button button-primary" disabled={!consented || saveProfile.isPending}>儲存基本資料</button></div>}
        </section>
      </form>

      <section className="application-actions">
        <div><p className="eyebrow">NEXT STEP / 下一步</p><h2>{applicationNextStep(status)}</h2></div>
        <div>
          {editable && <button className="button button-system" type="button" disabled={!application.data?.profile || submitApplication.isPending} onClick={() => submitApplication.mutate()}>{status === "needs_supplement" ? "重新送件" : "送出申請"}</button>}
          {["draft", "submitted", "needs_supplement", "approved"].includes(status) && application.data && <button className="button button-quiet" type="button" disabled={withdrawApplication.isPending} onClick={() => withdrawApplication.mutate()}>撤回申請</button>}
          {status === "approved" && <Link className="button button-primary" to="/account" search={{ membership_charge_id: undefined, payment: undefined }}>查看社員款項</Link>}
        </div>
        {(submitApplication.isError || withdrawApplication.isError) && <p className="form-error">{submitApplication.error?.message || withdrawApplication.error?.message}</p>}
      </section>
    </section>
  );
}

function applicationStatusLabel(status: string) {
  return { draft: "填寫中", submitted: "審核中", needs_supplement: "待補件", approved: "已核准", rejected: "未通過", withdrawn: "已撤回" }[status] || status;
}

function applicationNextStep(status: string) {
  return { draft: "完成基本資料後送出", submitted: "合作社正在審核", needs_supplement: "依審核意見補充基本資料", approved: "完成款項與後續程序", rejected: "請聯絡合作社確認後續", withdrawn: "申請已撤回" }[status] || "查看申請進度";
}
