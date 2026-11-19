import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { apiFetch } from "../lib/api";

type Props = {
  adminId: string;
  request: typeof apiFetch;
  source: "membership" | "roster";
  memberId: string;
};

export function AdminMemberPhone(props: Props) {
  const [open, setOpen] = useState(false);
  const valueId = useId();

  return <div className="admin-member-phone">
    <button type="button" className="button button-quiet"
      aria-expanded={open} aria-controls={open ? valueId : undefined} onClick={() => setOpen(current => !current)}>
      {open ? "隱藏電話" : "查看電話"}
    </button>
    {open && <MemberPhoneValue {...props} valueId={valueId} />}
  </div>;
}

function MemberPhoneValue({ adminId, request, source, memberId, valueId }: Props & { valueId: string }) {
  const phone = useQuery({
    queryKey: ["admin-member-phone", adminId, source, memberId],
    queryFn: ({ signal }) => request<{ phone: string | null }>(`/v1/admin/member-phone/${source}/${memberId}`, { signal, cache: "no-store" }),
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });

  if (phone.isError) return <p id={valueId} role="alert" className="form-error">電話無法讀取，請收起後重試。</p>;
  return <p id={valueId} role="status">{phone.isFetching || phone.isPending ? "讀取中…" : phone.data.phone || "未提供電話"}</p>;
}
