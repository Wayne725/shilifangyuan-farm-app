import { useState, type ReactNode } from "react";
import { AdminReasonChoices } from "./AdminReasonChoices";

const reasons: Record<string, string[]> = {
  reject: ["資格不符", "資料無法核對"], cancel: ["主辦單位取消", "場地無法使用"],
  "request-supplement": ["聯絡資料不完整", "社員資料需要補正"],
  activate: ["已核對名冊及線下繳費", "已完成資格審核"], suspend: ["社員申請暫停", "資格待重新核對"],
  resign: ["社員本人申請退社"], terminate: ["依合作社決議終止會籍"],
  "share-capital-return": ["已核對退社及股金返還資料"], close: ["決議已完成執行", "提案不再執行"],
};
export function SocialActionButton({ children, disabled, action, showReason, onConfirm }: {
  children: ReactNode; disabled: boolean;
  action: string; showReason: boolean; onConfirm: (reason: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  return <div className="social-action">
    <button type="button" disabled={disabled} aria-expanded={open} onClick={() => {setOpen(!open);}}>{children}</button>
    {open && <section className="social-action-confirm" aria-label="確認社務操作">
      <h4>確認：{children}</h4>
      {showReason && <AdminReasonChoices label="處理原因" value={reason} onChange={setReason} choices={(reasons[action] || ["已核對完成"]).map(value => ({label: value, value}))} disabled={disabled} />}
      <div className="admin-quick-buttons"><button type="button" className="button button-primary" disabled={disabled} onClick={() => {onConfirm(reason); setOpen(false);}}>確認送出</button><button type="button" className="button button-quiet" disabled={disabled} onClick={() => setOpen(false)}>返回</button></div>
    </section>}
  </div>;
}
