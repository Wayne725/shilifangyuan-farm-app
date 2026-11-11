import * as Dialog from '@radix-ui/react-dialog';
import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, apiFetch } from '../lib/api';
import './OrderCancellation.css';

export function CatalogVisibilityControl({ kind, record, onDone, onDenied }: {
  kind: 'activity' | 'group'; record: { id: string; is_public?: boolean; updated_at?: string };
  onDone: () => void; onDenied: () => void;
}) {
  const client = useQueryClient();
  const [conflict, setConflict] = useState(false);
  const [confirmationVersion, setConfirmationVersion] = useState<string | null>(null);
  const hidden = record.is_public === false;
  useEffect(() => setConflict(false), [record.updated_at]);
  useEffect(() => { if (hidden) setConfirmationVersion(null); }, [hidden]);
  const save = useMutation({
    mutationFn: (change: { is_public: boolean; expected_updated_at: string }) => apiFetch(kind === 'activity' ? `/v1/admin/activities/${record.id}/visibility` : `/v1/group-campaigns/${record.id}/admin/visibility`, {
      method: 'PATCH', body: JSON.stringify(change),
    }),
    onSuccess: () => {
      setConfirmationVersion(null);
      void client.invalidateQueries({ queryKey: [kind === 'activity' ? 'activities' : 'group-campaigns'] });
      onDone();
    },
    onError: error => {
      if (error instanceof ApiError && [401, 403].includes(error.status)) onDenied();
      else if (error instanceof ApiError && error.status === 409) { setConflict(true); onDone(); }
    },
  });
  const disabled = save.isPending || conflict || !record.updated_at;
  const changed = confirmationVersion !== null && confirmationVersion !== record.updated_at;

  return <div className="form-actions">
    {kind === 'group' && hidden && <span className="status-chip">未公開</span>}
    {kind === 'activity' && !hidden ? (
      <Dialog.Root open={confirmationVersion !== null} onOpenChange={open => {
        if (save.isPending) return;
        setConfirmationVersion(open ? record.updated_at ?? null : null);
        if (open) save.reset();
      }}>
        <Dialog.Trigger asChild><button type="button" className="button button-quiet" disabled={disabled}>停止公開</button></Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="admin-cancel-overlay" />
          <Dialog.Content className="admin-cancel-dialog">
            <Dialog.Title>停止公開活動</Dialog.Title>
            <Dialog.Description>活動將從公開頁面移除，報名紀錄會保留。</Dialog.Description>
            {changed ? <p className="form-error" role="alert">活動資料已更新，請返回後重新確認。</p> : save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
            <div className="form-actions">
              <Dialog.Close asChild><button type="button" className="button button-quiet" disabled={save.isPending}>返回</button></Dialog.Close>
              <button type="button" className="button button-primary" disabled={disabled || changed} onClick={() => {
                if (confirmationVersion && !disabled && !changed) save.mutate({ is_public: false, expected_updated_at: confirmationVersion });
              }}>{save.isPending ? '儲存中…' : '確認停止公開'}</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    ) : (
      <button type="button" className="button button-quiet" disabled={disabled} onClick={() => {
        if (record.updated_at) save.mutate({ is_public: hidden, expected_updated_at: record.updated_at });
      }}>{save.isPending ? '儲存中…' : hidden ? '恢復公開' : '停止公開'}</button>
    )}
    {conflict && <button type="button" className="button button-quiet" onClick={onDone}>重新讀取</button>}
    {confirmationVersion === null && save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
  </div>;
}
