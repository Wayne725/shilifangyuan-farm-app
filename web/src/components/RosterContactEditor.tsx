import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { apiFetch } from "../lib/api";

type Contact = { legal_name: string; email: string; phone: string; address: string; updated_at: string };

export function RosterContactEditor({ entryId, adminId, request }: { entryId: string; adminId: string; request: typeof apiFetch }) {
  const [open, setOpen] = useState(false);
  const client = useQueryClient();
  const path = `/v1/admin/member-roster/${entryId}/contact`;
  const key = ["admin-roster-contact", adminId, entryId];
  const contact = useQuery({ queryKey: key, queryFn: ({ signal }) => request<Contact>(path, { signal }),
    enabled: open, retry: false, gcTime: 0, refetchOnWindowFocus: false });
  return <section>
    <button className="button button-quiet" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? "收起名冊資料" : "更正名冊資料"}
    </button>
    {open && (contact.isFetching ? <p role="status">讀取名冊資料…</p>
      : contact.error ? <p className="form-error" role="alert">{contact.error.message}</p>
        : contact.data && <RosterContactForm contact={contact.data} request={request} path={path} onDone={async saved => {
          client.setQueryData(key, saved);
          await Promise.all(["admin-member-roster", "member-register"].map(item => client.invalidateQueries({ queryKey: [item] })));
          setOpen(false);
        }} />)}
  </section>;
}

function RosterContactForm({ contact, path, request, onDone }: {
  contact: Contact; path: string; request: typeof apiFetch; onDone: (saved: Contact) => Promise<void>;
}) {
  const [draft, setDraft] = useState(contact);
  const save = useMutation({
    mutationFn: () => request<Contact>(path, { method: "PATCH", body: JSON.stringify({
      legal_name: draft.legal_name.trim(), email: draft.email.trim(), phone: draft.phone.trim(),
      address: draft.address.trim(), expected_updated_at: draft.updated_at,
    }) }),
    onSuccess: onDone,
  });
  return <form className="social-form" aria-label="更正社員名冊" onSubmit={event => { event.preventDefault(); if (!save.isPending) save.mutate(); }}>
    <label className="field"><span>社員姓名</span><input required maxLength={80} value={draft.legal_name} onChange={event => setDraft({ ...draft, legal_name: event.target.value })} /></label>
    <label className="field"><span>聯絡 Email</span><input required type="email" value={draft.email} onChange={event => setDraft({ ...draft, email: event.target.value })} /></label>
    <label className="field"><span>聯絡電話</span><input required type="tel" maxLength={40} value={draft.phone} onChange={event => setDraft({ ...draft, phone: event.target.value })} /></label>
    <label className="field"><span>地址</span><input maxLength={500} value={draft.address} onChange={event => setDraft({ ...draft, address: event.target.value })} /></label>
    {save.error && <p className="form-error" role="alert">{save.error.message}</p>}
    <button className="button button-system" disabled={save.isPending}>儲存名冊更正</button>
  </form>;
}
