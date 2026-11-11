import { apiFetch } from "./api";

export type AdmissionStatus = "draft" | "submitted" | "needs_supplement" | "approved" | "rejected" | "withdrawn";

export interface AdmissionProfile {
  legal_name: string;
  phone: string;
  birth_date: string;
  address: string;
  emergency_contact: string;
  contact_email: string;
  gender: string | null;
  place_of_origin: string | null;
  occupation: string | null;
  registered_address: string | null;
  correspondence_address: string | null;
  landline_phone: string | null;
  line_id: string | null;
}

export interface AdmissionConfig {
  flow_version: string;
  material: { version: string; title: string; url: string; sections: { title: string; body: string }[] };
  quiz: {
    version: string;
    pass_score: number;
    points_per_question: number;
    questions: { id: string; question: string; options: { value: string; label: string }[] }[];
  };
  declaration: {
    version: string;
    privacy_version: string;
    privacy_url: string;
    rights: string[];
    obligations: string[];
    statement: string;
    admission_fee_amount?: number;
    share_unit_amount: number;
    student_min_shares: number;
    student_max_shares: number;
    general_min_shares: number;
    general_max_shares: number;
  };
  discovery_channels: string[];
}

export interface AdmissionQuizAttempt {
  id: string;
  attempt_number: number;
  quiz_version: string;
  score: number;
  passed: boolean;
  submitted_at: string;
  settings_version: number;
  results: { question_id: string; selected_option: string; correct_option: string; correct: boolean }[];
}

export interface AdmissionRound {
  id: string;
  user_id: string;
  round_number: number;
  version: number;
  flow_version: "education-review-v1";
  status: AdmissionStatus;
  profile: AdmissionProfile | null;
  requested_share_count: number;
  student_claimed: boolean;
  discovery_channel: string | null;
  discovery_other: string | null;
  declaration_version: string | null;
  privacy_version: string | null;
  consented_at: string | null;
  material_version: string | null;
  material_completed_at: string | null;
  quiz_version: string;
  quiz_eligibility: {
    allowed: boolean;
    remaining_attempts: number;
    next_available_at: string | null;
    reason: string | null;
    settings_configured: boolean;
  };
  attempts: AdmissionQuizAttempt[];
  submissions: { id: string; revision_number: number; submitted_at: string }[];
  reviews: { id: string; decision: string; reason: string; decision_reference: string | null; student_verified: boolean | null; reviewed_at: string }[];
  submitted_at: string | null;
  reviewed_at: string | null;
  review_reason: string | null;
  student_verified: boolean | null;
  decision_reference: string | null;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
  can_submit: boolean;
  can_withdraw: boolean;
}

export interface AdmissionRead {
  config: AdmissionConfig;
  current_round: AdmissionRound | null;
  history: { id: string; round_number: number; status: AdmissionStatus; created_at: string; reviewed_at: string | null }[];
  profile_defaults: AdmissionProfile | null;
  has_legacy_application?: boolean;
  can_start: boolean;
  start_blocked_reason: string | null;
}

export interface AdmissionDraft {
  profile: AdmissionProfile;
  requested_share_count: string;
  student_claimed: boolean;
  discovery_channel: string;
  discovery_other: string;
  declaration_accepted: boolean;
  privacy_accepted: boolean;
  share_rules_understood: boolean;
  payment_notice_understood: boolean;
}

export interface AdmissionSavePayload {
  expected_version: number;
  profile: AdmissionProfile;
  requested_share_count: number;
  student_claimed: boolean;
  discovery_channel: string;
  discovery_other: string | null;
  declaration_version: string;
  privacy_version: string;
  declaration_accepted: true;
  privacy_accepted: true;
  share_rules_understood: true;
  payment_notice_understood: true;
}

const admissionPath = "/v1/membership/admission";
const roundPath = (id: string) => `${admissionPath}/rounds/${encodeURIComponent(id)}`;

export const admissionApi = {
  read: () => apiFetch<AdmissionRead>(admissionPath),
  round: (id: string) => apiFetch<AdmissionRound>(roundPath(id)),
  start: (idempotency_key: string) => apiFetch<AdmissionRound>(`${admissionPath}/rounds`, { method: "POST", body: JSON.stringify({ idempotency_key }) }),
  save: (id: string, payload: AdmissionSavePayload) => apiFetch<AdmissionRound>(roundPath(id), { method: "PUT", body: JSON.stringify(payload) }),
  education: (id: string, expected_version: number, material_version: string) => apiFetch<AdmissionRound>(`${roundPath(id)}/education`, { method: "POST", body: JSON.stringify({ expected_version, material_version, read_confirmed: true }) }),
  quiz: (id: string, payload: { expected_version: number; idempotency_key: string; quiz_version: string; answers: { question_id: string; option: string }[] }) => apiFetch<AdmissionRound>(`${roundPath(id)}/quiz-attempts`, { method: "POST", body: JSON.stringify(payload) }),
  submit: (id: string, expected_version: number) => apiFetch<AdmissionRound>(`${roundPath(id)}/submit`, { method: "POST", body: JSON.stringify({ expected_version }) }),
  withdraw: (id: string, expected_version: number) => apiFetch<AdmissionRound>(`${roundPath(id)}/withdraw`, { method: "POST", body: JSON.stringify({ expected_version, reason: "申請人自行撤回" }) }),
};

export function admissionStatusLabel(status: string): string {
  return ({ draft: "填寫中", submitted: "審核中", needs_supplement: "待補件", approved: "審核同意", rejected: "未通過", withdrawn: "已撤回" } as Record<string, string>)[status] || status;
}

export function admissionDateTime(value: string | null): string {
  if (!value) return "尚未完成";
  return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

export function createAdmissionDraft(value: AdmissionRead, email: string): AdmissionDraft {
  const round = value.current_round;
  return {
    profile: round?.profile || value.profile_defaults || {
      legal_name: "", phone: "", birth_date: "", address: "", emergency_contact: "", contact_email: email,
      gender: null, place_of_origin: null, occupation: null, registered_address: null, correspondence_address: null, landline_phone: null, line_id: null,
    },
    requested_share_count: String(round?.requested_share_count || value.config.declaration.general_min_shares),
    student_claimed: round?.student_claimed || false,
    discovery_channel: round?.discovery_channel || "",
    discovery_other: round?.discovery_other || "",
    declaration_accepted: false,
    privacy_accepted: false,
    share_rules_understood: false,
    payment_notice_understood: false,
  };
}

export function admissionSavePayload(draft: AdmissionDraft, config: AdmissionConfig, version: number): AdmissionSavePayload {
  const profileKeys: (keyof AdmissionProfile)[] = ["legal_name", "phone", "birth_date", "address", "emergency_contact", "contact_email", "gender", "place_of_origin", "occupation", "registered_address", "correspondence_address", "landline_phone", "line_id"];
  const profile = Object.fromEntries(profileKeys.map(key => {
    const value = draft.profile[key];
    return [key, typeof value === "string" ? value.trim() : value];
  })) as unknown as AdmissionProfile;
  return {
    expected_version: version, profile, requested_share_count: Number(draft.requested_share_count), student_claimed: draft.student_claimed,
    discovery_channel: draft.discovery_channel, discovery_other: draft.discovery_channel === "其他" ? draft.discovery_other.trim() || null : null,
    declaration_version: config.declaration.version, privacy_version: config.declaration.privacy_version,
    declaration_accepted: true, privacy_accepted: true, share_rules_understood: true, payment_notice_understood: true,
  };
}
