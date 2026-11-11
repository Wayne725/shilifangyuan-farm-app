import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ApiError, type apiFetch } from "../lib/api";
import { readStoredJson, sessionStore, writeStoredJson } from "../lib/browser-storage";

interface Adjustment {
  request_id: string;
  member_number: string;
  amount: number;
  expected_balance: number;
}
interface PendingAdjustment extends Adjustment {
  reasonFingerprint: string;
  hasReason: boolean;
}
export interface PointAdjustmentReceipt {
  id: string;
  user_id: string;
  member_number: string;
  amount: number;
  balance_before: number;
  balance_after: number;
}

export const signedPoints = (amount: number) => `${amount > 0 ? "+" : ""}${amount}`;

function readPending(key: string): PendingAdjustment | null {
  const value = readStoredJson<PendingAdjustment | null>(sessionStore, key, null);
  return value && typeof value.request_id === "string" && /^[a-f0-9-]{36}$/.test(value.request_id)
    && typeof value.member_number === "string" && Number.isSafeInteger(value.amount)
    && Number.isSafeInteger(value.expected_balance) && typeof value.reasonFingerprint === "string"
    && typeof value.hasReason === "boolean" ? value : null;
}

async function fingerprint(requestId: string, reason: string): Promise<string> {
  const bytes = new TextEncoder().encode(`${requestId}\n${reason}`);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function MemberPointAdjustment({ adminId, target, request, onClose, onSaved, onStale }: {
  adminId: string;
  target: { user_id: string; member_number: string; name: string; balance: number };
  request: typeof apiFetch;
  onClose: () => void;
  onSaved: (receipt: PointAdjustmentReceipt) => void;
  onStale: () => Promise<void>;
}) {
  const key = `member-point-adjustment-v1:${adminId}:${target.user_id}`;
  const path = `/v1/admin/points-ledger/${encodeURIComponent(target.user_id)}/adjustments`;
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<Adjustment | null>(null);
  const [pending, setPending] = useState(() => readPending(key));
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);

  const finish = (receipt: PointAdjustmentReceipt) => {
    sessionStore.removeItem(key);
    onSaved(receipt);
  };
  const lookup = useMutation({
    mutationFn: (id: string) => request<PointAdjustmentReceipt>(`${path}/${encodeURIComponent(id)}`, { cache: "no-store" }),
    onSuccess: finish,
  });
  const save = useMutation({
    mutationFn: async (action: Adjustment) => {
      const text = reason.trim();
      const reasonFingerprint = await fingerprint(action.request_id, text);
      if (pending && reasonFingerprint !== pending.reasonFingerprint) {
        throw new Error("請輸入與原提交相同的調整原因。");
      }
      if (!pending) {
        const snapshot = { ...action, reasonFingerprint, hasReason: Boolean(text) };
        if (!writeStoredJson(sessionStore, key, snapshot)) {
          sessionStore.removeItem(key);
          throw new Error("無法保存本次調整的查核識別，請重新開啟帳冊再試。");
        }
        setPending(snapshot);
      }
      return request<PointAdjustmentReceipt>(path, {
        method: "POST",
        body: JSON.stringify({
          request_id: action.request_id, member_number: action.member_number,
          amount: action.amount, expected_balance: action.expected_balance, reason: text,
        }),
      });
    },
    onSuccess: finish,
    onError: async (error) => {
      if (error instanceof ApiError && ["point_balance_changed", "point_target_changed"].includes(error.code ?? "")) {
        sessionStore.removeItem(key);
        setPending(null);
        setPreview(null);
        await onStale();
      }
    },
  });
  const delta = Number(amount);
  const valid = /^[+-]?\d+$/.test(amount.trim()) && delta !== 0
    && delta >= -2_147_483_648 && delta <= 2_147_483_647
    && Number.isSafeInteger(target.balance) && Number.isSafeInteger(target.balance + delta);
  const confirmation = pending ?? preview;
  const busy = save.isPending || lookup.isPending;
  const failure = save.error ?? lookup.error;

  return <section className="admin-records-form member-point-adjustment" aria-labelledby="member-point-adjustment-heading">
    <h2 id="member-point-adjustment-heading" tabIndex={-1} ref={heading}>調整社員點數</h2>
    <p>{target.name} · 社員編號 {confirmation?.member_number ?? target.member_number}</p>
    {!confirmation ? <form onSubmit={(event) => {
      event.preventDefault();
      if (valid && !busy) {
        save.reset();
        setPreview({ request_id: crypto.randomUUID(), member_number: target.member_number,
          amount: delta, expected_balance: target.balance });
      }
    }}>
      <label className="field"><span>增減點數</span><input required value={amount} placeholder="+10 或 -10" onChange={(event) => setAmount(event.target.value)} /></label>
      <label className="field"><span>調整原因</span><input value={reason} maxLength={240} onChange={(event) => setReason(event.target.value)} /></label>
      <div className="form-actions">
        <button className="button button-primary" disabled={!valid || busy}>預覽調整</button>
        <button className="button button-quiet" type="button" onClick={onClose}>取消</button>
      </div>
    </form> : <>
      <dl className="admin-records-totals">
        <div><dt>調整前</dt><dd>{confirmation.expected_balance}</dd></div>
        <div><dt>增減點數</dt><dd>{signedPoints(confirmation.amount)}</dd></div>
        <div><dt>調整後</dt><dd>{confirmation.expected_balance + confirmation.amount}</dd></div>
      </dl>
      {pending ? <>
        <p>這筆調整的入帳結果待確認。</p>
        {pending.hasReason && <label className="field"><span>原調整原因</span><input value={reason} maxLength={240} disabled={busy} onChange={(event) => setReason(event.target.value)} /></label>}
      </> : reason.trim() && <p>調整原因 {reason.trim()}</p>}
      <div className="form-actions">
        <button className="button button-primary" type="button" disabled={busy || Boolean(pending?.hasReason && !reason.trim())} onClick={() => save.mutate(confirmation)}>
          {save.isPending ? "送出中…" : pending ? "重試原調整" : "確認調整"}
        </button>
        {pending && <button type="button" className="button button-quiet" disabled={busy} onClick={() => lookup.mutate(pending.request_id)}>查詢入帳結果</button>}
        <button className="button button-quiet" type="button" disabled={busy} onClick={() => {
          if (pending) onClose();
          else setPreview(null);
        }}>{pending ? "關閉" : "返回修改"}</button>
      </div>
    </>}
    {failure && <p className="form-error" role="alert">{failure instanceof ApiError && failure.status >= 500
      ? "尚未確認入帳，請查詢入帳結果或重試原調整。" : failure.message}</p>}
  </section>;
}
