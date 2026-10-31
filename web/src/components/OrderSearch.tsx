export function OrderSearch({ query, payment, onQuery, onPayment, count, searching = false, refreshing = false, onRefresh }: {
  query: string; payment: string; onQuery: (value: string) => void; onPayment: (value: string) => void; count: number;
  searching?: boolean; refreshing?: boolean; onRefresh?: () => void;
}) {
  return <div className="order-search">
    <label className="field"><span>查詢訂單</span><input type="search" value={query} onChange={(event) => onQuery(event.target.value)} placeholder="訂單編號、餐點／商品名稱、取貨地點" /></label>
    <label className="field"><span>付款狀態</span><select value={payment} onChange={(event) => onPayment(event.target.value)}>
      <option value="">全部狀態</option><option value="pending">待付款</option><option value="paid">已付款</option><option value="refund_pending">退款中</option><option value="refunded">已退款</option><option value="failed">付款失敗</option><option value="expired">已逾期</option><option value="late_paid_refund_required">付款晚到待退款</option>
    </select></label>
    <button className="button button-quiet" type="button" onClick={() => { onQuery(""); onPayment(""); }}>清除查詢</button>
    {onRefresh && <button className="button button-quiet" type="button" disabled={refreshing} onClick={onRefresh}>{refreshing ? "更新中…" : "更新訂單"}</button>}
    <p className="order-search-count" role="status">{searching ? "正在查詢訂單…" : `已載入 ${count} 筆訂單`}</p>
  </div>;
}
