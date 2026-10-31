import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { ApiError, apiFetch } from "./api";
import { filterOrders } from "./order-search";
import { flattenHistoryPages, nextHistoryCursor, normalizeHistoryQuery, orderHistoryPageSize, orderHistoryPath, orderNeedsRefresh, replaceHistoryOrder, type HistoryCursor, type HistoryKind, type HistoryOrder } from "./order-history";

const retryHistory = (attempt: number, error: Error) => !(error instanceof ApiError && [401, 403, 404].includes(error.status)) && attempt < 2;

export function useOrderHistory<T extends HistoryOrder>(kind: HistoryKind, userId: string | undefined,
  query: string, payment: string, selectedId: string, requestedId?: string, active = true) {
  const client = useQueryClient();
  const normalized = normalizeHistoryQuery(query);
  const [search, setSearch] = useState(normalized);
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(normalized), 250);
    return () => window.clearTimeout(timer);
  }, [normalized]);
  const prefix = kind === "meal" ? "meal-orders" : "orders";
  const key = useMemo(() => [prefix, userId, "history", search, payment], [prefix, userId, search, payment]);
  const history = useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam }) => apiFetch<T[]>(orderHistoryPath(kind, search, payment, pageParam)),
    initialPageParam: null as HistoryCursor | null,
    getNextPageParam: (page: T[]) => nextHistoryCursor(page),
    enabled: Boolean(userId && active),
    retry: retryHistory,
    refetchOnWindowFocus: true,
    refetchInterval: state => {
      if (state.state.error instanceof ApiError && [401, 403].includes(state.state.error.status)) return false;
      const pages = state.state.data?.pages;
      return pages?.length === 1 && pages[0].length <= orderHistoryPageSize && pages[0].some(orderNeedsRefresh) ? 5000 : false;
    },
  });
  const loaded = flattenHistoryPages(history.data?.pages || []);
  const returned = loaded.find(order => order.id === requestedId);
  const readOrder = async (id: string) => {
    try { return await apiFetch<T>(`/v1/${kind === "meal" ? "meal-orders" : "orders"}/${id}${kind === "general" ? "?own_only=true" : ""}`); }
    catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  };
  const canReadTarget = Boolean(userId && active && requestedId && history.data && !returned && !search && !payment);
  const target = useQuery({
    queryKey: [prefix, userId, "detail", requestedId],
    queryFn: () => readOrder(requestedId!),
    enabled: canReadTarget,
    retry: retryHistory,
    refetchInterval: state => canReadTarget && requestedId === selectedId && state.state.data && orderNeedsRefresh(state.state.data) ? 5000 : false,
  });
  const targetOrder = returned || target.data || undefined;
  const withTarget = targetOrder && !returned && !search && !payment ? [...loaded, targetOrder] : loaded;
  const items = filterOrders(withTarget, search, payment);
  const selected = items.find(order => order.id === selectedId) || items[0];
  const multiPage = Boolean(history.data && (history.data.pages.length > 1 || history.data.pages[0]?.length > orderHistoryPageSize));
  const current = useQuery({
    queryKey: [prefix, userId, "detail", selected?.id],
    queryFn: () => readOrder(selected!.id),
    enabled: Boolean(userId && active && multiPage && selected && selected.id !== (returned ? undefined : requestedId)),
    retry: retryHistory,
    staleTime: 0,
    refetchInterval: state => state.state.data && orderNeedsRefresh(state.state.data) ? 5000 : false,
  });
  useEffect(() => {
    if (target.data) client.setQueryData<InfiniteData<T[]>>(key, data => replaceHistoryOrder(data, target.data!));
  }, [client, key, target.data]);
  useEffect(() => {
    if (current.data) client.setQueryData<InfiniteData<T[]>>(key, data => replaceHistoryOrder(data, current.data!));
  }, [client, key, current.data]);
  const refresh = () => {
    void history.refetch();
    if (canReadTarget) void target.refetch();
    if (multiPage && selected && selected.id !== (returned ? undefined : requestedId)) void current.refetch();
  };
  return { ...history, items, returnedOrder: targetOrder, targetError: target.error, selectedError: current.error,
    returnedReading: Boolean((!history.data && history.isPending) || (returned && history.isFetching && !history.isFetchingNextPage)
      || (!returned && target.isFetching) || (selected?.id === requestedId && current.isFetching)),
    returnedError: target.error || (history.isError && !history.isFetchNextPageError ? history.error : null)
      || (selected?.id === requestedId ? current.error : null),
    targetPending: Boolean(canReadTarget && target.isPending), searching: normalized !== search,
    refresh, hasLoaded: Boolean(history.data) };
}
