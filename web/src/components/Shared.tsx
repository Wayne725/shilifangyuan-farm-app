import { ArrowClockwise, ArrowRight, Basket, Plus } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

import { formatMoney, replaceBrokenAsset, resolveAsset } from "../lib/api";
import type { Product } from "../lib/types";

export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  action?: { label: string; to: "/" | "/shop" | "/social" | "/checkout" | "/admin" };
}) {
  return (
    <div className="section-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        {description && <p className="section-description">{description}</p>}
      </div>
      {action && (
        <Link className="text-link" to={action.to}>
          {action.label}
          <ArrowRight size={18} weight="light" />
        </Link>
      )}
    </div>
  );
}

export function ProductCard({
  product,
  onAdd,
}: {
  product: Product;
  onAdd: (product: Product) => void;
}) {
  return (
    <article className="product-card">
      <Link className="product-image-frame" to="/products/$productId" params={{ productId: product.id }}>
        <img src={resolveAsset(product.image_url)} alt={product.name} onError={replaceBrokenAsset} />
        <span>{product.category}</span>
      </Link>
      <div className="product-copy">
        <div className="product-meta">
          <span>{product.supplier_name || "合作社共同選品"}</span>
          <span>{product.sku || product.product_number || "依批次供應"}</span>
        </div>
        <h3><Link to="/products/$productId" params={{ productId: product.id }}>{product.name}</Link></h3>
        <p>{product.description || "產地與批次資訊由合作社維護。"}</p>
        <div className="product-bottom">
          <div>
            <span className="price">{formatMoney(product.member_price)}</span>
            <small>社員價／{product.unit}</small>
          </div>
          <button
            className="icon-button add-button"
            type="button"
            onClick={() => onAdd(product)}
            disabled={product.stock_quantity <= 0}
            aria-label={`加入 ${product.name}`}
          >
            <Plus size={20} weight="light" />
          </button>
        </div>
      </div>
    </article>
  );
}

export function DataState({
  title,
  detail,
  kind = "empty",
  actionLabel = "重新連線",
  onAction,
}: {
  title: string;
  detail: string;
  kind?: "empty" | "error";
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className={`data-state ${kind}`}>
      <Basket size={28} weight="light" />
      <div>
        <h3>{title}</h3>
        <p>{detail}</p>
        {onAction && (
          <button className="data-state-action" type="button" onClick={onAction}>
            <ArrowClockwise size={15} />
            {actionLabel}
          </button>
        )}
      </div>
    </div>
  );
}

export function LoadingLines({ count = 3 }: { count?: number }) {
  return (
    <div className="loading-lines" aria-label="載入中">
      {Array.from({ length: count }, (_, index) => (
        <span key={index} />
      ))}
    </div>
  );
}
