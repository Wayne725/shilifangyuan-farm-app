import * as Dialog from "@radix-ui/react-dialog";
import { Printer, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../context/AuthContext";
import { ApiError } from "../lib/api";
import { useAdminPanelAccess } from "../lib/admin-panel-access";
import type { Order } from "../lib/types";
import "./AdminOrderPrint.css";

const privateQueryKeys = [] as const;

type Job = { id: string; status: string; mode: "live" | "dry_run"; kind: string; created_at: string };
type Preview = {
  preview: string; preview_token: string; kind: string; revision: number;
  mode: "live" | "dry_run"; can_print: boolean; blocked_reason: string | null;
  duplicate_risk: boolean; jobs: Job[];
};
type Attempt = { request_id: string; preview_token: string; reason: string; acknowledge_duplicate: boolean };
const statuses: Record<string, string> = {
  pending: "等待發送", sending: "正在發送", submitted: "雲端已接受", printed: "已確認列印",
  failed: "出單失敗", unknown: "結果待確認", simulated: "模擬完成",
};

function readAttempt(key: string): Attempt | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null") as Attempt | null;
    return value && typeof value.request_id === "string" && typeof value.preview_token === "string"
      && typeof value.reason === "string" && typeof value.acknowledge_duplicate === "boolean" ? value : null;
  } catch { return null; }
}

export function AdminOrderPrint({ order }: { order: Pick<Order, "id" | "order_number" | "sales_channel" | "payment_status" | "paid_at" | "cancelled_at" | "fulfillment_status" | "fulfillment"> }) {
  const { user } = useAuth();
  const access = useAdminPanelAccess(privateQueryKeys);
  const client = useQueryClient();
  const eligible = order.payment_status === "paid" && Boolean(order.paid_at) && !order.cancelled_at
    && order.fulfillment_status !== "cancelled" && order.fulfillment?.status !== "cancelled";
  const storageKey = `admin-order-print:${user?.id}:${order.id}`;
  const [attempt, setAttempt] = useState<Attempt | null>(() => readAttempt(storageKey));
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [reason, setReason] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const [another, setAnother] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const busyRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const saveAttempt = (value: Attempt | null) => {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
    setAttempt(value);
  };
  const operate = async (action: () => Promise<void>) => {
    if (access.isDenied() || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (failure) { if (mounted.current && !access.isDenied()) setError(failure instanceof Error ? failure.message : "無法完成出單操作。"); }
    finally { busyRef.current = false; if (mounted.current && !access.isDenied()) setBusy(false); }
  };
  const loadPreview = () => operate(async () => {
    setPreview(null); setReason(""); setAcknowledge(false); setAnother(false);
    const value = await access.request<Preview>(`/v1/admin/printers/orders/${order.id}/preview`);
    if (mounted.current && !access.isDenied()) setPreview(value);
  });
  const findJob = () => operate(async () => {
    if (!attempt) return;
    try {
      const value = await access.request<Job>(`/v1/admin/printers/orders/${order.id}/print-requests/${attempt.request_id}`);
      if (mounted.current && !access.isDenied()) setJob(value);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 404) throw new Error(eligible ? "尚未查到這筆送印，可重試同一筆送印。" : "尚未查到這筆送印；訂單目前不符出單條件，請核對原送印結果。");
      throw failure;
    }
  });
  const send = () => operate(async () => {
    if (!eligible) return;
    if (!attempt && (!preview?.can_print || (preview.duplicate_risk && !acknowledge))) return;
    const value = attempt || { request_id: crypto.randomUUID(), preview_token: preview!.preview_token, reason: reason.trim(), acknowledge_duplicate: acknowledge };
    // Keep the request across lost responses, dialog closes and page reloads.
    saveAttempt(value);
    try {
      const result = await access.request<Job>(`/v1/admin/printers/orders/${order.id}/print`, { method: "POST", body: JSON.stringify(value) });
      if (mounted.current && !access.isDenied()) { setJob(result); setPreview(null); }
      if (!access.isDenied()) await client.invalidateQueries({ queryKey: ["print-jobs"] });
    } catch (failure) {
      if (failure instanceof ApiError && [400, 409, 422].includes(failure.status) && mounted.current && !access.isDenied()) {
        saveAttempt(null); setPreview(null);
      }
      throw failure;
    }
  });
  const settled = job && ["printed", "failed", "simulated"].includes(job.status);
  useEffect(() => {
    if (access.isDenied() || !open || !attempt || !job || settled) return;
    let active = true;
    const timer = setInterval(() => {
      if (access.isDenied()) return;
      void access.request<Job>(`/v1/admin/printers/orders/${order.id}/print-requests/${attempt.request_id}`)
        .then(value => { if (active && !access.isDenied()) setJob(value); })
        .catch(() => { /* Manual refresh exposes errors without discarding the last known state. */ });
    }, 4000);
    return () => { active = false; clearInterval(timer); };
  }, [open, order.id, attempt?.request_id, job?.id, settled, access.denied, access.isDenied, access.request]);

  if (user?.user_role !== "admin" || order.sales_channel !== "meal_preorder" || (!eligible && !attempt)) return null;
  if (access.denied) return <div className="admin-gate"><p role="alert">管理權限已失效或已變更，請重新驗證後再操作。</p><button className="button button-system" type="button" onClick={() => window.location.reload()}>重新驗證管理權限</button></div>;
  return <div className="admin-order-print">
    <Dialog.Root open={open} onOpenChange={next => {
      if (access.isDenied() || busyRef.current) return;
      setOpen(next);
      if (next) { setError(""); if (attempt) void findJob(); else void loadPreview(); }
    }}>
      <Dialog.Trigger asChild><button type="button" className="button button-quiet"><Printer size={20} />{eligible ? "再次出單" : "查詢送印結果"}</button></Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="admin-order-print-overlay" />
        <Dialog.Content className="admin-order-print-dialog" aria-describedby={undefined}
          onEscapeKeyDown={event => { if (busy) event.preventDefault(); }}
          onPointerDownOutside={event => { if (busy) event.preventDefault(); }}>
          <Dialog.Title>訂單出單／補印</Dialog.Title>
          <p className="order-number">{order.order_number}</p>
          {preview && !attempt && <>
            <p><strong>{preview.kind === "reference" ? "訂單紀錄單／請勿製作" : "便當訂餐單"}</strong> · 第 {preview.revision} 版</p>
            {preview.mode === "dry_run" && <p role="status">目前為模擬模式，不會送到出單機。</p>}
            <pre aria-label="出單預覽">{preview.preview}</pre>
            {preview.blocked_reason && <p className="form-error" role="status">{preview.blocked_reason}</p>}
            <form onSubmit={event => { event.preventDefault(); void send(); }}>
              <label className="field"><span>出單原因</span><input maxLength={200} value={reason} disabled={busy || !preview.can_print} onChange={event => setReason(event.target.value)} /></label>
              {preview.duplicate_risk && <label className="admin-order-print-check"><input type="checkbox" required checked={acknowledge} disabled={busy} onChange={event => setAcknowledge(event.target.checked)} />已核對紙本，接受可能重複列印</label>}
              <button className="button button-primary" disabled={busy || !preview.can_print || (preview.duplicate_risk && !acknowledge)}>{busy ? "送印中…" : preview.mode === "dry_run" ? "確認模擬出單" : "確認送至出單機"}</button>
            </form>
          </>}
          {job && <div role="status" className="admin-order-print-result"><strong>已建立出單工作：{statuses[job.status] || job.status}</strong><span>{job.mode === "dry_run" ? "模擬" : "正式"} · {job.kind === "reference" ? "訂單紀錄單／請勿製作" : "便當訂餐單"}</span></div>}
          {attempt && !job && <p role="status">{eligible ? "這筆送印結果待確認，請查詢或重試同一筆送印。" : "訂單目前不符出單條件，僅可查詢原送印結果。"}</p>}
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="admin-order-print-actions">
            {!attempt && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void loadPreview()}>{busy ? "讀取中…" : "重新產生預覽"}</button>}
            {attempt && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void findJob()}>查詢送印結果</button>}
            {eligible && attempt && !job && <button type="button" className="button button-primary" disabled={busy} onClick={() => void send()}>重試同一筆送印</button>}
          </div>
          {eligible && job && !["pending", "sending"].includes(job.status) && <div className="admin-order-print-another">
            <label className="admin-order-print-check"><input type="checkbox" checked={another} disabled={busy} onChange={event => setAnother(event.target.checked)} />已核對紙本，需要再印一份</label>
            <button type="button" className="button button-quiet" disabled={busy || !another} onClick={() => { saveAttempt(null); setJob(null); void loadPreview(); }}>再印一份</button>
          </div>}
          <Dialog.Close asChild><button type="button" className="icon-button admin-order-print-close" disabled={busy} aria-label="關閉訂單出單"><X size={20} /></button></Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </div>;
}
