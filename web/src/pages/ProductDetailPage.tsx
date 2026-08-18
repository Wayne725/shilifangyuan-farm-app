import { ArrowLeft, Basket, Package, SealCheck, Truck } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";

import { DataState, LoadingLines } from "../components/Shared";
import { useCommerce } from "../context/CommerceContext";
import { apiFetch, formatMoney, replaceBrokenAsset, resolveAsset } from "../lib/api";
import { shippingChannelLabels, temperatureLabels } from "../lib/commerce";
import type { Product } from "../lib/types";

export function ProductDetailPage() {
  const { productId } = useParams({ from: "/products/$productId" });
  const { addToCart } = useCommerce();
  const product = useQuery({ queryKey: ["product", productId], queryFn: () => apiFetch<Product>(`/v1/products/${productId}`) });
  if (product.isPending) return <section className="offer-page"><LoadingLines count={4} /></section>;
  if (product.isError || !product.data) return <section className="offer-page"><DataState kind="error" title="找不到這項商品" detail={product.error?.message || "商品可能已下架"} /></section>;
  const item = product.data;
  return <section className="offer-page product-detail-page"><Link className="text-link" to="/shop"><ArrowLeft size={17} />回到合作社選品</Link><div className="product-detail-grid"><div className="product-detail-image"><img src={resolveAsset(item.image_url)} alt={item.name} onError={replaceBrokenAsset} /><span>{item.category}</span></div><div className="product-detail-copy"><p className="eyebrow">COOPERATIVE PROVISION</p><h1>{item.name}</h1><p className="product-detail-description">{item.description || "商品來源、批次與供應條件由合作社維護。"}</p><dl><div><dt>供應者</dt><dd>{item.supplier_name || "合作社共同選品"}</dd></div><div><dt>商品編號</dt><dd>{item.product_number || "依批次編列"}</dd></div><div><dt>SKU</dt><dd>{item.sku || "未編列"}</dd></div><div><dt>稅別</dt><dd>{item.tax_type === "taxable" ? "應稅" : "免稅"}</dd></div></dl><div className="product-detail-prices"><span><small>社員價</small><strong>{formatMoney(item.member_price)}</strong></span><span><small>一般價</small><strong>{formatMoney(item.nonmember_price)}</strong></span><span><small>現有庫存</small><strong>{item.stock_quantity} {item.unit}</strong></span></div><button className="button button-primary full-width" type="button" disabled={item.stock_quantity <= 0} onClick={() => addToCart(item)}><Basket size={18} />{item.stock_quantity > 0 ? "加入結帳清單" : "目前缺貨"}</button></div></div><div className="product-detail-notes"><article><SealCheck size={24} weight="light" /><div><h2>正式主檔</h2><p>價格、稅別、SKU 與供應者皆以後台資料為準。</p></div></article><article><Package size={24} weight="light" /><div><h2>取貨方式</h2><p>可在合作社取貨點領取，結帳時選擇適合地點。</p></div></article><article><Truck size={24} weight="light" /><div><h2>物流配送</h2><p>{item.can_ship ? `${temperatureLabels[item.shipping_temperature || "ambient"]} · ${item.allowed_shipping_channels.map((channel) => shippingChannelLabels[channel]).join("、")}` : "本商品目前僅開放合作社取貨。"}</p></div></article></div></section>;
}
