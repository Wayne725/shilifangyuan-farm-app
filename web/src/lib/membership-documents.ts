import type { MembershipDocument } from "./types";

export function documentExpired(document: MembershipDocument): boolean {
  return Boolean(document.retention_expired || (document.expires_at && Date.parse(document.expires_at) <= Date.now()));
}

export function documentAvailable(document: MembershipDocument): boolean {
  return document.status === "confirmed" && !documentExpired(document);
}

export function documentRetentionLabel(document: MembershipDocument): string {
  if (document.deleted_at || document.status === "deleted") return "證件檔案已刪除";
  if (documentExpired(document)) return document.deletion_error ? "已停止調閱，刪除失敗待重試" : "已到期停止調閱，等待清除";
  if (!document.expires_at) return "舊資料：留存期限待確認";
  const deadline = new Date(document.expires_at).toLocaleString("zh-TW", { hour12: false });
  return document.status === "pending_upload" ? `請於 ${deadline} 前完成上傳確認` : `留存至 ${deadline}`;
}
