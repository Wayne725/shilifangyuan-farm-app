import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { apiFetch } from "../lib/api";
export function useStorefrontStatus() {
  return useQuery({ queryKey: ["public-platform"], queryFn: () => apiFetch<{ orders_open: boolean; closed_message: string }>("/v1/platform"), refetchInterval: 30000, staleTime: 15000, retry: false });
}
export function StorefrontClosed({ message }: { message: string }) {
  return <section className="account-gate"><p className="eyebrow">ORDERING PAUSED / 暫停接單</p><h1>{message}</h1><Link className="button button-quiet" to="/orders">查看既有訂單</Link></section>;
}
