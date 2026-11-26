import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

export function DeleteRecordAction({ label, detail, remove, onDeleted, disabled = false }: {
  label: string;
  detail: string;
  remove: () => Promise<unknown>;
  onDeleted: () => void;
  disabled?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const deletion = useMutation({ mutationFn: remove, onSuccess: () => { setConfirming(false); onDeleted(); } });
  if (!confirming) {
    return <button className="button button-quiet" type="button" disabled={disabled}
      onClick={() => { deletion.reset(); setConfirming(true); }}>{label}</button>;
  }
  return <div role="group" aria-label={label}>
    <p>{detail}只有從沒被使用過的資料才能刪除，刪除後無法復原。</p>
    <div className="form-actions">
      <button className="button button-quiet" type="button" disabled={deletion.isPending} onClick={() => setConfirming(false)}>取消</button>
      <button className="button button-danger" type="button" disabled={deletion.isPending} onClick={() => deletion.mutate()}>
        {deletion.isPending ? "刪除中…" : `確認${label}`}
      </button>
    </div>
    {deletion.error && <p className="form-error" role="alert">{deletion.error.message}</p>}
  </div>;
}
