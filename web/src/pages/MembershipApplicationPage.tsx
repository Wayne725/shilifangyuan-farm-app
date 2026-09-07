import { CheckCircle, FileArrowUp, IdentificationCard, ShieldWarning, Trash, UserCircle } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, type FormEvent, useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";

import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { ApiError, apiFetch } from "../lib/api";
import type { MembershipApplication, MembershipDocument, MembershipSummary } from "../lib/types";

type DocumentType = MembershipDocument["document_type"];
type UploadTicket = {
  document_id: string;
  upload_url: string;
  required_headers: Record<string, string>;
};

const emptyForm = {
  legal_name: "",
  phone: "",
  birth_date: "",
  address: "",
  emergency_contact: "",
  identity_number: "",
  gender: "",
  place_of_origin: "",
  occupation: "",
  registered_address: "",
  correspondence_address: "",
  landline_phone: "",
  line_id: "",
};

const documentSlots: Array<{ type: DocumentType; label: string }> = [
  { type: "id_front", label: "身分證正面" },
  { type: "id_back", label: "身分證反面" },
  { type: "secondary", label: "第二證件" },
];
const isDemoEnvironment = import.meta.env.VITE_APP_ENV !== "production";

export function MembershipApplicationPage() {
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [consented, setConsented] = useState(false);
  const [uploading, setUploading] = useState<DocumentType | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [testDocumentConfirmed, setTestDocumentConfirmed] = useState(false);
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
      identity_number: profile.identity_number || "",
      gender: profile.gender || "",
      place_of_origin: profile.place_of_origin || "",
      occupation: profile.occupation || "",
      registered_address: profile.registered_address || "",
      correspondence_address: profile.correspondence_address || "",
      landline_phone: profile.landline_phone || "",
      line_id: profile.line_id || "",
    });
    setConsented(true);
  }, [application.data?.profile]);

  const saveProfile = useMutation({
    mutationFn: () => apiFetch<MembershipApplication>("/v1/membership/application", {
      method: "PUT",
      body: JSON.stringify({
        ...form,
        consent_version: application.data?.profile?.consent_version || "membership-data-v1",
        identity_number: form.identity_number || null,
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
  const deleteDocument = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/membership/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["membership-application"] }),
  });

  function save(event: FormEvent) {
    event.preventDefault();
    saveProfile.mutate();
  }

  async function uploadDocument(type: DocumentType, event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadError("");
    if (file.size === 0 || file.size > 8 * 1024 * 1024) {
      setUploadError("檔案大小須為 1 byte 至 8MB，請重新選擇");
      event.target.value = "";
      return;
    }
    if (!["image/jpeg", "image/png", "application/pdf"].includes(file.type)) {
      setUploadError("僅支援 JPEG、PNG 或 PDF 檔案");
      event.target.value = "";
      return;
    }
    setUploading(type);
    try {
      const buffer = await file.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", buffer);
      const checksum = Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
      const ticket = await apiFetch<UploadTicket>("/v1/membership/documents/upload-url", {
        method: "POST",
        body: JSON.stringify({ document_type: type, content_type: file.type, size_bytes: file.size, checksum_sha256: checksum }),
      });
      const uploadResponse = await fetch(ticket.upload_url, { method: "PUT", headers: ticket.required_headers, body: file });
      if (!uploadResponse.ok) throw new Error("證件上傳失敗，請重新選擇檔案");
      await apiFetch(`/v1/membership/documents/${ticket.document_id}/confirm`, { method: "POST", body: JSON.stringify({ checksum_sha256: checksum }) });
      await queryClient.invalidateQueries({ queryKey: ["membership-application"] });
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "證件上傳失敗");
    } finally {
      setUploading(null);
      event.target.value = "";
    }
  }

  if (!user) {
    return <section className="account-gate"><UserCircle size={48} weight="light" /><p className="eyebrow">MEMBERSHIP</p><h1>開始入社申請</h1><p>登入後填寫資料並查看申請進度。</p><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></section>;
  }
  if (membership.isPending || application.isPending) return <section className="membership-page"><LoadingLines count={5} /></section>;
  if (membership.data?.membership_type === "member") {
    return <section className="membership-page"><header className="membership-heading"><CheckCircle size={42} weight="light" /><p className="eyebrow">ACTIVE MEMBER</p><h1>你已是正式社員</h1><p>{membership.data.membership?.member_number || "社員資格已啟用"}</p><div><Link className="button button-primary" to="/social">進入社務系統</Link><Link className="button button-quiet" to="/account" search={{ membership_charge_id: undefined, payment: undefined }}>查看社員中心</Link></div></header></section>;
  }

  const status = application.data?.status || "draft";
  const editable = ["draft", "needs_supplement"].includes(status);
  const confirmedCount = documentSlots.filter(({ type }) => application.data?.documents.some((document) => document.document_type === type && document.status === "confirmed")).length;

  return (
    <section className="membership-page">
      <header className="workspace-heading membership-workspace-heading">
        <div><p className="eyebrow">MEMBERSHIP APPLICATION</p><h1>入社申請</h1></div>
        <div className="application-status"><span>{applicationStatusLabel(status)}</span><strong>{confirmedCount}/3</strong><small>證件已確認</small></div>
      </header>

      {application.data?.review_reason && <div className="return-banner failed"><IdentificationCard size={22} /><div><strong>審核意見</strong><span>{application.data.review_reason}</span></div></div>}

      <form className="membership-form" onSubmit={save}>
        <section>
          <div className="form-heading"><div><p className="eyebrow">PROFILE</p><h2>基本資料</h2></div></div>
          <div className="field-grid three-columns">
            <label className="field"><span>姓名</span><input required disabled={!editable} value={form.legal_name} onChange={(event) => setForm({ ...form, legal_name: event.target.value })} /></label>
            <label className="field"><span>身分證字號</span><input disabled={!editable} value={form.identity_number} onChange={(event) => setForm({ ...form, identity_number: event.target.value })} /></label>
            <label className="field"><span>出生日期</span><input required disabled={!editable} type="date" value={form.birth_date} onChange={(event) => setForm({ ...form, birth_date: event.target.value })} /></label>
            <label className="field"><span>性別</span><input disabled={!editable} value={form.gender} onChange={(event) => setForm({ ...form, gender: event.target.value })} /></label>
            <label className="field"><span>籍貫</span><input disabled={!editable} value={form.place_of_origin} onChange={(event) => setForm({ ...form, place_of_origin: event.target.value })} /></label>
            <label className="field"><span>職業</span><input disabled={!editable} value={form.occupation} onChange={(event) => setForm({ ...form, occupation: event.target.value })} /></label>
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

      <section className="document-section">
        <div className="form-heading"><div><p className="eyebrow">DOCUMENTS</p><h2>身分證件</h2></div><span>JPEG、PNG 或 PDF，單檔 8MB 以內</span></div>
        {isDemoEnvironment && (
          <div className="document-safety-notice" role="note">
            <ShieldWarning size={24} weight="light" />
            <div>
              <strong>展示環境禁止上傳真實身分證件</strong>
              <p>請只使用自行製作、沒有真實姓名與證號的測試檔案。</p>
              <label>
                <input
                  type="checkbox"
                  checked={testDocumentConfirmed}
                  onChange={(event) => setTestDocumentConfirmed(event.target.checked)}
                />
                我確認本次只會上傳測試檔案
              </label>
            </div>
          </div>
        )}
        <div className="document-grid">
          {documentSlots.map((slot) => {
            const document = application.data?.documents.find((item) => item.document_type === slot.type);
            return (
              <article key={slot.type} className={document?.status === "confirmed" ? "confirmed" : ""}>
                {document?.status === "confirmed" ? <CheckCircle size={28} weight="light" /> : <FileArrowUp size={28} weight="light" />}
                <h3>{slot.label}</h3>
                <span>{document?.status === "confirmed" ? "已確認" : uploading === slot.type ? "上傳中" : "尚未上傳"}</span>
                {editable && <label className="button button-quiet"><input type="file" accept="image/jpeg,image/png,application/pdf" disabled={uploading !== null || (isDemoEnvironment && !testDocumentConfirmed)} onChange={(event) => uploadDocument(slot.type, event)} />{document ? "重新上傳" : "選擇檔案"}</label>}
                {document && editable && <button className="document-delete" type="button" aria-label={`刪除${slot.label}`} disabled={deleteDocument.isPending} onClick={() => deleteDocument.mutate(document.id)}><Trash size={16} /></button>}
              </article>
            );
          })}
        </div>
        {uploadError && <p className="form-error">{uploadError}</p>}
      </section>

      <section className="application-actions">
        <div><p className="eyebrow">NEXT STEP</p><h2>{applicationNextStep(status)}</h2></div>
        <div>
          {editable && <button className="button button-system" type="button" disabled={!application.data?.profile || confirmedCount < 3 || submitApplication.isPending} onClick={() => submitApplication.mutate()}>{status === "needs_supplement" ? "重新送件" : "送出申請"}</button>}
          {["draft", "submitted", "needs_supplement", "approved"].includes(status) && application.data && <button className="button button-quiet" type="button" disabled={withdrawApplication.isPending} onClick={() => withdrawApplication.mutate()}>撤回申請</button>}
          {["submitted", "approved"].includes(status) && <Link className="button button-primary" to="/account" search={{ membership_charge_id: undefined, payment: undefined }}>查看社員款項</Link>}
        </div>
        {(submitApplication.isError || withdrawApplication.isError || deleteDocument.isError) && <p className="form-error">{submitApplication.error?.message || withdrawApplication.error?.message || deleteDocument.error?.message}</p>}
      </section>
    </section>
  );
}

function applicationStatusLabel(status: string) {
  return { draft: "填寫中", submitted: "審核中", needs_supplement: "待補件", approved: "已核准", rejected: "未通過", withdrawn: "已撤回" }[status] || status;
}

function applicationNextStep(status: string) {
  return { draft: "完成資料與證件後送出", submitted: "合作社正在審核", needs_supplement: "依審核意見完成補件", approved: "完成款項與後續程序", rejected: "請聯絡合作社確認後續", withdrawn: "申請已撤回" }[status] || "查看申請進度";
}
