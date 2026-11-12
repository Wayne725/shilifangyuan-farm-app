import type { CalendarBlank } from "@phosphor-icons/react";
import type { ReactNode } from "react";

import { DataState, LoadingLines } from "./Shared";

export function AdminReviewSection({ icon: Icon, title, count, pending, error, empty, children }: { icon: typeof CalendarBlank; title: string; count: number; pending: boolean; error?: string; empty: string; children: ReactNode }) {
  return <section className="admin-review-section"><div className="section-title-row"><div><h2>{title}</h2></div><span><Icon size={20} />{count}</span></div>{pending && <LoadingLines count={2} />}{error && <DataState kind="error" title={`${title}無法讀取`} detail={error} />}{!pending && !error && count === 0 && <DataState title={empty} detail="" />}<div className="admin-review-list">{children}</div></section>;
}
