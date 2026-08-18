import { ArrowSquareOut, HandHeart, Plus } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "@tanstack/react-router";

import { SocialNav } from "../components/SocialNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import type { MembershipSummary, Wish } from "../lib/types";

const emptyForm = { name: "", description: "", expected_price: "", reference_url: "" };

export function WishesPage() {
  const { user, openLogin } = useAuth();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const membership = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
  const canAccess = membership.data?.membership_type === "member";
  const wishes = useQuery({
    queryKey: ["wishes"],
    queryFn: () => apiFetch<Wish[]>("/v1/wishes"),
    enabled: canAccess,
  });
  const createWish = useMutation({
    mutationFn: () => apiFetch<Wish>("/v1/wishes", {
      method: "POST",
      body: JSON.stringify({
        name: form.name,
        description: form.description,
        expected_price: form.expected_price ? Number(form.expected_price) : null,
        reference_url: form.reference_url || null,
      }),
    }),
    onSuccess: () => {
      setForm(emptyForm);
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["wishes"] });
    },
  });
  const supportWish = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/wishes/${id}/support`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["wishes"] }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    createWish.mutate();
  }

  return (
    <section className="social-page-shell">
      <header className="social-page-heading">
        <div><p className="eyebrow">COLLECTIVE WISHLIST</p><h1>社員願望</h1></div>
        {canAccess && <button className="button button-system" type="button" onClick={() => setShowForm((value) => !value)}><Plus size={18} />提出願望</button>}
      </header>
      <SocialNav />

      {!user ? (
        <div className="social-access-card"><HandHeart size={38} weight="light" /><h2>登入後查看社員願望</h2><button className="button button-primary" type="button" onClick={openLogin}>登入帳號</button></div>
      ) : membership.isPending ? <LoadingLines count={3} /> : !canAccess ? (
        <div className="social-access-card"><HandHeart size={38} weight="light" /><h2>此功能開放給正式社員</h2><Link className="button button-system" to="/membership">查看入社進度</Link></div>
      ) : (
        <>
          {showForm && (
            <form className="social-form" onSubmit={submit}>
              <div className="form-heading"><div><p className="eyebrow">NEW WISH</p><h2>想找什麼商品？</h2></div></div>
              <div className="field-grid two-columns">
                <label className="field wide"><span>商品或服務名稱</span><input required maxLength={160} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
                <label className="field"><span>期望價格</span><input min={0} inputMode="numeric" type="number" value={form.expected_price} onChange={(event) => setForm({ ...form, expected_price: event.target.value })} /></label>
                <label className="field"><span>參考連結</span><input maxLength={500} type="url" placeholder="https://" value={form.reference_url} onChange={(event) => setForm({ ...form, reference_url: event.target.value })} /></label>
                <label className="field wide"><span>需求說明</span><textarea required rows={5} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
              </div>
              {createWish.isError && <p className="form-error">{createWish.error.message}</p>}
              <div className="form-actions"><button className="button button-quiet" type="button" onClick={() => setShowForm(false)}>取消</button><button className="button button-primary" disabled={createWish.isPending}>送出願望</button></div>
            </form>
          )}

          {wishes.isPending && <LoadingLines count={4} />}
          {wishes.isError && <DataState kind="error" title="願望暫時無法讀取" detail={wishes.error.message} />}
          {wishes.data?.length === 0 && <DataState title="願望池還是空的" detail="提出你希望合作社一起尋找的商品或服務。" />}
          <div className="wish-grid">
            {wishes.data?.map((wish) => (
              <article key={wish.id}>
                <header><span className="status-chip">{wishStatusLabel(wish.status)}</span><time>{formatDate(wish.created_at)}</time></header>
                <h2>{wish.name}</h2>
                <p>{wish.description}</p>
                {wish.expected_price != null && <strong className="wish-price">期望 {formatMoney(wish.expected_price)}</strong>}
                {wish.reference_url && <a className="text-link" href={wish.reference_url} target="_blank" rel="noreferrer">查看參考資料<ArrowSquareOut size={15} /></a>}
                {wish.admin_note && <blockquote>{wish.admin_note}</blockquote>}
                <footer><span><HandHeart size={18} weight={wish.supported_by_me ? "fill" : "light"} />{wish.support_count} 位社員支持</span><button className={`button ${wish.supported_by_me ? "button-quiet" : "button-system"}`} type="button" disabled={wish.supported_by_me || supportWish.isPending} onClick={() => supportWish.mutate(wish.id)}>{wish.supported_by_me ? "已支持" : "支持願望"}</button></footer>
              </article>
            ))}
          </div>
          {supportWish.isError && <p className="form-error">{supportWish.error.message}</p>}
        </>
      )}
    </section>
  );
}

function wishStatusLabel(status: string) {
  return { submitted: "新提出", gathering: "蒐集中", sourcing: "尋找中", launched: "已成案", declined: "不採用" }[status] || status;
}
