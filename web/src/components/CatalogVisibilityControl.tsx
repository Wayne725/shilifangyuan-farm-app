import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, apiFetch } from '../lib/api';

export function CatalogVisibilityControl({ kind, record, onDone, onDenied }: {
  kind: 'activity' | 'group'; record: { id: string; is_public?: boolean; updated_at?: string };
  onDone: () => void; onDenied: () => void;
}) {
  const client = useQueryClient();
  const [conflict, setConflict] = useState(false);
  useEffect(() => setConflict(false), [record.updated_at]);
  const save = useMutation({
    mutationFn: () => apiFetch(kind === 'activity' ? `/v1/admin/activities/${record.id}/visibility` : `/v1/group-campaigns/${record.id}/admin/visibility`, {
      method: 'PATCH', body: JSON.stringify({ is_public: record.is_public === false, expected_updated_at: record.updated_at }),
    }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [kind === 'activity' ? 'activities' : 'group-campaigns'] });
      onDone();
    },
    onError: error => {
      if (error instanceof ApiError && [401, 403].includes(error.status)) onDenied();
      else if (error instanceof ApiError && error.status === 409) { setConflict(true); onDone(); }
    },
  });
  return <div className="form-actions">
    {record.is_public === false && <span className="status-chip">未公開</span>}
    <button type="button" className="button button-quiet" disabled={save.isPending || conflict || !record.updated_at} onClick={() => save.mutate()}>{save.isPending ? '儲存中…' : record.is_public === false ? '恢復公開' : '停止公開'}</button>
    {conflict && <button type="button" className="button button-quiet" onClick={onDone}>重新讀取</button>}
    {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
  </div>;
}
