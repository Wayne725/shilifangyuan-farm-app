import { Plus } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { meetingAccessDenied, retryMeetingRead } from "../lib/meetings";
import { ApiError, apiFetch } from "../lib/api";
import type { MeetingCategory } from "../lib/types";
import { DataState, LoadingLines } from "./Shared";
import "./MeetingTools.css";

const categoryKey = ["admin-meeting-categories"];
const readCategories = () => apiFetch<MeetingCategory[]>("/v1/admin/meeting-categories");
const validSort = (value: string) => value.trim() !== "" && Number.isInteger(Number(value)) && Number(value) >= 0 && Number(value) <= 2147483647;

export function MeetingFilters({ categories, categoryId, keyword, onCategory, onKeyword }: {
  categories: Array<{ id: string; name: string }>; categoryId: string; keyword: string;
  onCategory: (value: string) => void; onKeyword: (value: string) => void;
}) {
  return <div className="meeting-filters" role="search" aria-label="查詢會議">
    <label className="field"><span>會議類別</span><select aria-label="查詢會議類別" value={categoryId} onChange={event => onCategory(event.target.value)}><option value="">全部類別</option>{categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
    <label className="field"><span>查詢會議</span><input type="search" aria-label="查詢會議關鍵字" placeholder="名稱、地點、議程或決議" value={keyword} onChange={event => onKeyword(event.target.value)} /></label>
    <button className="button button-quiet" type="button" onClick={() => { onCategory(""); onKeyword(""); }} disabled={!categoryId && !keyword}>清除查詢</button>
  </div>;
}

export function MeetingSetup({ onDone, onAccessDenied }: { onDone: () => void; onAccessDenied: () => void }) {
  const queryClient = useQueryClient();
  const categories = useQuery({ queryKey: categoryKey, queryFn: readCategories, retry: retryMeetingRead });
  const [result, setResult] = useState("");
  useEffect(() => {
    if (meetingAccessDenied(categories.error)) onAccessDenied();
  }, [categories.error, onAccessDenied]);
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: categoryKey }),
      queryClient.invalidateQueries({ queryKey: ["meeting-categories"] }),
      queryClient.invalidateQueries({ queryKey: ["meetings"] }),
      queryClient.invalidateQueries({ queryKey: ["admin-meetings"] }),
    ]);
  };
  if (meetingAccessDenied(categories.error)) return <DataState kind="error" title="會議類別管理權限已失效" detail="請重新登入管理員帳號" />;
  return <div className="meeting-setup">
    <details className="meeting-category-panel">
      <summary>會議類別</summary>
      <div className="meeting-category-body">
        {categories.isPending ? <LoadingLines count={2} /> : categories.isError ? <DataState kind="error" title="會議類別無法讀取" detail={categories.error.message} /> : <>
          <CategoryCreate onAccessDenied={onAccessDenied} onDone={async () => { await refresh(); setResult("會議類別已新增"); }} />
          {categories.data.length === 0 && <p role="status">尚未建立會議類別</p>}
          <div className="meeting-category-list">{categories.data.map(category => <CategoryEditor onAccessDenied={onAccessDenied} key={category.id} category={category} onDone={async () => { await refresh(); setResult("會議類別已更新"); }} />)}</div>
        </>}
        {categories.isError && <button className="button button-quiet" type="button" onClick={() => void categories.refetch()}>重新讀取類別</button>}
        {result && <p role="status" className="form-success">{result}</p>}
      </div>
    </details>
    <MeetingCreateForm onAccessDenied={onAccessDenied} categories={categories.isError ? [] : categories.data || []} loading={categories.isPending} error={categories.error?.message} onDone={onDone} />
  </div>;
}

function CategoryCreate({ onDone, onAccessDenied }: { onDone: () => Promise<void>; onAccessDenied: () => void }) {
  const [name, setName] = useState("");
  const [sort, setSort] = useState("0");
  const valid = Boolean(name.trim()) && validSort(sort);
  const create = useMutation({
    mutationFn: () => apiFetch<MeetingCategory>("/v1/admin/meeting-categories", { method: "POST", body: JSON.stringify({ name: name.trim(), sort_order: Number(sort), is_active: true }) }),
    onSuccess: async () => { await onDone(); setName(""); setSort("0"); },
    onError: error => { if (meetingAccessDenied(error)) onAccessDenied(); },
  });
  return <form className="meeting-category-create" aria-label="新增會議類別" onSubmit={event => { event.preventDefault(); if (valid && !create.isPending) create.mutate(); }}>
    <label className="field"><span>新類別名稱</span><input required maxLength={80} value={name} disabled={create.isPending} onChange={event => setName(event.target.value)} /></label>
    <label className="field"><span>排序</span><input aria-label="新類別排序" required type="number" min={0} max={2147483647} step={1} value={sort} disabled={create.isPending} onChange={event => setSort(event.target.value)} /></label>
    <button className="button button-system" type="submit" disabled={!valid || create.isPending}><Plus size={17} />{create.isPending ? "新增中…" : "新增類別"}</button>
    {create.isError && <p className="form-error" role="alert">{create.error.message}</p>}
  </form>;
}

function CategoryEditor({ category, onDone, onAccessDenied }: { category: MeetingCategory; onDone: () => Promise<void>; onAccessDenied: () => void }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(category.name);
  const [sort, setSort] = useState(String(category.sort_order));
  const [active, setActive] = useState(category.is_active);
  const [version, setVersion] = useState(category.updated_at);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<MeetingCategory | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const valid = Boolean(name.trim()) && validSort(sort);
  const ready = conflict ? Boolean(latest && confirmed) : Boolean(version);
  const save = useMutation({
    mutationFn: () => apiFetch<MeetingCategory>(`/v1/admin/meeting-categories/${category.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim(), sort_order: Number(sort), is_active: active, expected_updated_at: conflict ? latest?.updated_at : version }) }),
    onSuccess: async () => { await onDone(); setOpen(false); },
    onError: error => { if (meetingAccessDenied(error)) onAccessDenied(); else if (error instanceof ApiError && error.status === 409) { setConflict(true); setLatest(null); setConfirmed(false); } },
  });
  const reload = useMutation({
    mutationFn: async () => {
      const current = await queryClient.fetchQuery({ queryKey: categoryKey, queryFn: readCategories, staleTime: 0, retry: retryMeetingRead });
      const record = current.find(item => item.id === category.id);
      if (!record) throw new Error("找不到最新類別，請重新讀取會議類別");
      return record;
    },
    onSuccess: record => { setLatest(record); setConfirmed(false); },
    onError: error => { if (meetingAccessDenied(error)) onAccessDenied(); },
  });
  const begin = () => {
    setName(category.name); setSort(String(category.sort_order)); setActive(category.is_active); setVersion(category.updated_at);
    setConflict(false); setLatest(null); setConfirmed(false); save.reset(); reload.reset(); setOpen(true);
  };
  const busy = save.isPending || reload.isPending;
  return <article className="meeting-category-row" aria-label={`類別 ${category.name}`}>
    <div className="meeting-category-summary"><strong>{category.name}</strong><span>{category.is_active ? "啟用" : "停用"} · 排序 {category.sort_order}</span>{!open && <button className="button button-quiet" type="button" onClick={begin}>編輯</button>}</div>
    {open && <form aria-label={`編輯類別 ${category.name}`} onSubmit={event => { event.preventDefault(); if (valid && ready && !busy) save.mutate(); }}>
      <div className="meeting-category-fields">
        <label className="field"><span>類別名稱</span><input required maxLength={80} value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label>
        <label className="field"><span>排序</span><input required type="number" min={0} max={2147483647} step={1} value={sort} disabled={busy} onChange={event => setSort(event.target.value)} /></label>
        <label className="field"><span>類別狀態</span><select aria-label="類別狀態" value={active ? "active" : "inactive"} disabled={busy} onChange={event => setActive(event.target.value === "active")}><option value="active">啟用</option><option value="inactive">停用</option></select></label>
      </div>
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
      {conflict && <div className="meeting-category-conflict">
        <p className="form-error">類別未能儲存，輸入已保留。請核對最新資料後再儲存。</p>
        <button className="button button-quiet" type="button" disabled={busy} onClick={() => { setLatest(null); setConfirmed(false); reload.mutate(); }}>重新讀取最新類別</button>
        {latest && <><p>目前類別：{latest.name} · 排序 {latest.sort_order} · {latest.is_active ? "啟用" : "停用"}</p><label className="meal-choice"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /><span>已核對最新類別，確認儲存目前輸入</span></label></>}
        {reload.isError && <p className="form-error" role="alert">{reload.error.message}</p>}
      </div>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={busy} onClick={() => setOpen(false)}>取消編輯</button><button className="button button-primary" type="submit" disabled={!valid || !ready || busy}>{save.isPending ? "儲存中…" : "儲存類別"}</button></div>
    </form>}
  </article>;
}

function MeetingCreateForm({ categories, loading, error, onDone, onAccessDenied }: { categories: MeetingCategory[]; loading: boolean; error?: string; onDone: () => void; onAccessDenied: () => void }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const empty = { category_id: "", title: "", agenda: "", starts_at: "", location: "" };
  const [form, setForm] = useState(empty);
  const [saved, setSaved] = useState(false);
  const activeCategories = categories.filter(category => category.is_active);
  const categoryValid = activeCategories.some(category => category.id === form.category_id);
  const valid = categoryValid && Boolean(form.title.trim()) && Boolean(form.starts_at) && Boolean(form.location.trim());
  const create = useMutation({
    mutationFn: () => apiFetch("/v1/admin/meetings", { method: "POST", body: JSON.stringify({ ...form, title: form.title.trim(), location: form.location.trim(), starts_at: new Date(form.starts_at).toISOString(), agenda: form.agenda.split("\n").map(title => title.trim()).filter(Boolean).map(title => ({ title })) }) }),
    onSuccess: () => { setOpen(false); setForm(empty); setSaved(true); onDone(); },
    onError: error => { if (meetingAccessDenied(error)) onAccessDenied(); else if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: categoryKey }); },
  });
  return <section className="meeting-create-section">
    <button className="button button-system" type="button" onClick={() => { setOpen(value => !value); setSaved(false); }} disabled={create.isPending}><Plus size={17} />{open ? "收起建立會議" : "建立會議"}</button>
    {saved && <p className="form-success" role="status">會議已建立</p>}
    {open && <form className="social-form" aria-label="建立各項會議" onSubmit={event => { event.preventDefault(); if (valid && !create.isPending) create.mutate(); }}>
      <div className="form-heading"><div><p className="eyebrow">NEW MEETING / 新增會議</p><h2>建立各項會議</h2></div></div>
      {loading ? <LoadingLines count={1} /> : error ? <p className="form-error" role="alert">會議類別無法讀取：{error}</p> : activeCategories.length === 0 && <p className="form-error" role="alert">沒有啟用的會議類別，請先新增或啟用類別。</p>}
      <div className="field-grid two-columns">
        <label className="field"><span>會議類別</span><select required aria-label="建立會議類別" disabled={loading || Boolean(error) || create.isPending} value={form.category_id} onChange={event => setForm({ ...form, category_id: event.target.value })}><option value="">選擇類別</option>{form.category_id && !categoryValid && <option value={form.category_id} disabled>原選類別已停用，請重新選擇</option>}{activeCategories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
        <label className="field"><span>會議名稱</span><input required maxLength={160} disabled={create.isPending} value={form.title} onChange={event => setForm({ ...form, title: event.target.value })} /></label>
        <label className="field"><span>開始時間</span><input required type="datetime-local" disabled={create.isPending} value={form.starts_at} onChange={event => setForm({ ...form, starts_at: event.target.value })} /></label>
        <label className="field"><span>地點</span><input required maxLength={240} disabled={create.isPending} value={form.location} onChange={event => setForm({ ...form, location: event.target.value })} /></label>
        <label className="field wide"><span>議程（每行一項）</span><textarea rows={5} disabled={create.isPending} value={form.agenda} onChange={event => setForm({ ...form, agenda: event.target.value })} /></label>
      </div>
      {create.isError && <p className="form-error" role="alert">{create.error.message}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" disabled={create.isPending} onClick={() => setOpen(false)}>取消</button><button className="button button-primary" type="submit" disabled={!valid || create.isPending}>{create.isPending ? "建立中…" : "確認建立會議"}</button></div>
    </form>}
  </section>;
}
