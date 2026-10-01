import * as Tabs from "@radix-ui/react-tabs";
import {
  Buildings,
  MapPin,
  Plus,
  SealCheck,
} from "@phosphor-icons/react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { AdminNav } from "../components/AdminNav";
import { DataState, LoadingLines } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { apiFetch, formatDate, formatMoney } from "../lib/api";
import { shippingChannelLabels, temperatureLabels } from "../lib/commerce";
import type {
  PickupLocation,
  Product,
  ShippingChannel,
  ShippingTemperature,
  Supplier,
  SupplierAccreditationStatus,
} from "../lib/types";

const productCategories = [
  ["當季蔬果", "當季蔬果"],
  ["米・雜糧", "米・雜糧"],
  ["蛋品", "蛋品"],
  ["加工品", "加工品"],
  ["飲品", "飲品"],
  ["生活用品", "生活用品"],
] as const;

const shippingChannels: ShippingChannel[] = [
  "home_delivery",
  "seven_eleven",
  "family_mart",
  "hilife",
];

interface ProductDraft {
  product_number: string;
  sku: string;
  supplier_id: string;
  name: string;
  description: string;
  category: string;
  unit: string;
  image_url: string;
  member_price: number;
  nonmember_price: number;
  stock_quantity: number;
  tax_type: "taxable" | "tax_exempt";
  can_ship: boolean;
  shipping_temperature: ShippingTemperature;
  allowed_shipping_channels: ShippingChannel[];
  is_active: boolean;
}

const emptyProduct: ProductDraft = {
  product_number: "",
  sku: "",
  supplier_id: "",
  name: "",
  description: "",
  category: "當季蔬果",
  unit: "份",
  image_url: "",
  member_price: 0,
  nonmember_price: 0,
  stock_quantity: 0,
  tax_type: "taxable",
  can_ship: false,
  shipping_temperature: "ambient",
  allowed_shipping_channels: [],
  is_active: true,
};

interface SupplierDraft {
  supplier_number: string;
  business_name: string;
  tax_id: string;
  responsible_person: string;
  contact_person: string;
  phone: string;
  email: string;
  line_id: string;
  settlement_terms: string;
  bank_account: string;
  is_active: boolean;
}

const emptySupplier: SupplierDraft = {
  supplier_number: "",
  business_name: "",
  tax_id: "",
  responsible_person: "",
  contact_person: "",
  phone: "",
  email: "",
  line_id: "",
  settlement_terms: "",
  bank_account: "",
  is_active: false,
};

export function AdminCatalogPage() {
  const { user, openLogin } = useAuth();
  const isAdmin = user?.user_role === "admin";
  const queryClient = useQueryClient();
  const [products, suppliers, locations] = useQueries({
    queries: [
      { queryKey: ["admin-products"], queryFn: () => apiFetch<Product[]>("/v1/products"), enabled: isAdmin },
      { queryKey: ["admin-suppliers"], queryFn: () => apiFetch<Supplier[]>("/v1/admin/suppliers"), enabled: isAdmin },
      { queryKey: ["admin-pickup-locations"], queryFn: () => apiFetch<PickupLocation[]>("/v1/admin/pickup-locations"), enabled: isAdmin },
    ],
  });

  if (!isAdmin) {
    return (
      <section className="admin-gate">
        <div className="admin-gate-mark"><Buildings size={38} weight="light" /></div>
        <p className="eyebrow">CATALOG OPERATIONS</p>
        <h1>商品與供應管理只向管理者開放。</h1>
        {!user ? <button className="button button-primary" type="button" onClick={openLogin}>管理者登入</button> : <Link className="button button-quiet" to="/shop">返回生活消費</Link>}
      </section>
    );
  }

  const refreshProducts = () => queryClient.invalidateQueries({ queryKey: ["admin-products"] });
  const refreshSuppliers = () => queryClient.invalidateQueries({ queryKey: ["admin-suppliers"] });
  const refreshLocations = () => queryClient.invalidateQueries({ queryKey: ["admin-pickup-locations"] });

  return (
    <section className="admin-module-page">
      <header className="workspace-heading">
        <div><p className="eyebrow">CATALOG OPERATIONS / 商品供應</p><h1>商品、供應者與取貨點</h1></div>
        <p>以正式編號、SKU、審認紀錄與物流條件維護營運主檔。</p>
      </header>
      <AdminNav />

      <Tabs.Root className="admin-social-tabs" defaultValue="products">
        <Tabs.List className="tab-list" aria-label="商品供應分類">
          <Tabs.Trigger value="products">商品主檔</Tabs.Trigger>
          <Tabs.Trigger value="suppliers">供應者</Tabs.Trigger>
          <Tabs.Trigger value="locations">取貨點</Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content className="tab-content" value="products">
          <ProductCreateForm suppliers={suppliers.data || []} onDone={refreshProducts} />
          <AdminSection title="商品清單" count={products.data?.length || 0} pending={products.isPending} error={products.error?.message}>
            <div className="admin-record-grid">
              {products.data?.map((product) => (
                <ProductEditor key={product.id} product={product} suppliers={suppliers.data || []} onDone={refreshProducts} />
              ))}
            </div>
          </AdminSection>
        </Tabs.Content>

        <Tabs.Content className="tab-content" value="suppliers">
          <SupplierCreateForm onDone={refreshSuppliers} />
          <AdminSection title="供應者清單" count={suppliers.data?.length || 0} pending={suppliers.isPending} error={suppliers.error?.message}>
            <div className="admin-review-list">
              {suppliers.data?.map((supplier) => <SupplierEditor key={supplier.id} supplier={supplier} onDone={refreshSuppliers} />)}
            </div>
          </AdminSection>
        </Tabs.Content>

        <Tabs.Content className="tab-content" value="locations">
          <LocationCreateForm onDone={refreshLocations} />
          <AdminSection title="取貨點清單" count={locations.data?.length || 0} pending={locations.isPending} error={locations.error?.message}>
            <div className="admin-record-grid">
              {locations.data?.map((location) => <LocationEditor key={location.id} location={location} onDone={refreshLocations} />)}
            </div>
          </AdminSection>
        </Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

function AdminSection({ title, count, pending, error, children }: { title: string; count: number; pending: boolean; error?: string; children: React.ReactNode }) {
  return (
    <section className="admin-review-section">
      <div className="section-title-row"><div><p className="eyebrow">MASTER DATA</p><h2>{title}</h2></div><span>{count}</span></div>
      {pending && <LoadingLines count={3} />}
      {error && <DataState kind="error" title={`${title}無法讀取`} detail={error} />}
      {!pending && !error && count === 0 && <DataState title={`目前沒有${title}`} detail="建立後會出現在這裡。" />}
      {children}
    </section>
  );
}

function ProductCreateForm({ suppliers, onDone }: { suppliers: Supplier[]; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ProductDraft>(emptyProduct);
  const create = useMutation({
    mutationFn: () => apiFetch<Product>("/v1/products", { method: "POST", body: JSON.stringify(productPayload(draft)) }),
    onSuccess: () => { setDraft(emptyProduct); setOpen(false); onDone(); },
  });
  return (
    <section className="admin-create-block">
      <button className="button button-system" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />新增商品</button>
      {open && <ProductForm title="上架新商品" draft={draft} suppliers={suppliers} busy={create.isPending} error={create.error?.message} onChange={setDraft} onCancel={() => setOpen(false)} onSubmit={() => create.mutate()} />}
    </section>
  );
}

function ProductEditor({ product, suppliers, onDone }: { product: Product; suppliers: Supplier[]; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ProductDraft>(() => ({
    product_number: product.product_number || "", sku: product.sku || "", supplier_id: product.supplier_id || "", name: product.name,
    description: product.description, category: product.category, unit: product.unit, image_url: product.image_url || "",
    member_price: product.member_price, nonmember_price: product.nonmember_price, stock_quantity: product.stock_quantity,
    tax_type: product.tax_type, can_ship: product.can_ship, shipping_temperature: product.shipping_temperature || "ambient",
    allowed_shipping_channels: product.allowed_shipping_channels, is_active: product.is_active,
  }));
  const update = useMutation({
    mutationFn: () => apiFetch<Product>(`/v1/products/${product.id}`, { method: "PATCH", body: JSON.stringify(productPayload(draft)) }),
    onSuccess: () => { setEditing(false); onDone(); },
  });
  if (editing) return <ProductForm title={product.name} draft={draft} suppliers={suppliers} busy={update.isPending} error={update.error?.message} onChange={setDraft} onCancel={() => setEditing(false)} onSubmit={() => update.mutate()} />;
  return (
    <article className="admin-record-card">
      <span className={`status-chip ${product.is_active ? "passed" : ""}`}>{product.is_active ? "上架" : "下架"}</span>
      <h3>{product.name}</h3>
      <p>{product.supplier_name || "未指定供應者"} · {product.sku || product.product_number || "未編號"}</p>
      <dl><div><dt>社員價</dt><dd>{formatMoney(product.member_price)}</dd></div><div><dt>庫存</dt><dd>{product.stock_quantity} {product.unit}</dd></div><div><dt>物流</dt><dd>{product.can_ship ? temperatureLabels[product.shipping_temperature || "ambient"] : "僅取貨"}</dd></div></dl>
      <button type="button" onClick={() => setEditing(true)}>編輯商品</button>
    </article>
  );
}

function ProductForm({ title, draft, suppliers, busy, error, onChange, onCancel, onSubmit }: { title: string; draft: ProductDraft; suppliers: Supplier[]; busy: boolean; error?: string; onChange: (draft: ProductDraft) => void; onCancel: () => void; onSubmit: () => void }) {
  const submit = (event: FormEvent) => { event.preventDefault(); onSubmit(); };
  const toggleChannel = (channel: ShippingChannel) => onChange({ ...draft, allowed_shipping_channels: draft.allowed_shipping_channels.includes(channel) ? draft.allowed_shipping_channels.filter((item) => item !== channel) : [...draft.allowed_shipping_channels, channel] });
  return (
    <form className="social-form admin-master-form" onSubmit={submit}>
      <div className="form-heading"><div><p className="eyebrow">PRODUCT RECORD</p><h2>{title}</h2></div><span>社員價不可高於一般價。</span></div>
      <div className="field-grid three-columns">
        <label className="field"><span>產品編號</span><input value={draft.product_number} onChange={(event) => onChange({ ...draft, product_number: event.target.value })} /></label>
        <label className="field"><span>SKU</span><input value={draft.sku} onChange={(event) => onChange({ ...draft, sku: event.target.value })} /></label>
        <label className="field"><span>供應者</span><select value={draft.supplier_id} onChange={(event) => onChange({ ...draft, supplier_id: event.target.value })}><option value="">未指定</option>{suppliers.filter((supplier) => supplier.is_active).map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.business_name}</option>)}</select></label>
        <label className="field"><span>商品名稱</span><input required value={draft.name} onChange={(event) => onChange({ ...draft, name: event.target.value })} /></label>
        <label className="field"><span>分類</span><select value={draft.category} onChange={(event) => onChange({ ...draft, category: event.target.value })}>{productCategories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="field"><span>販售單位</span><input required value={draft.unit} onChange={(event) => onChange({ ...draft, unit: event.target.value })} /></label>
        <label className="field"><span>社員價</span><input min={0} type="number" value={draft.member_price} onChange={(event) => onChange({ ...draft, member_price: Number(event.target.value) })} /></label>
        <label className="field"><span>一般價</span><input min={0} type="number" value={draft.nonmember_price} onChange={(event) => onChange({ ...draft, nonmember_price: Number(event.target.value) })} /></label>
        <label className="field"><span>庫存</span><input min={0} type="number" value={draft.stock_quantity} onChange={(event) => onChange({ ...draft, stock_quantity: Number(event.target.value) })} /></label>
        <label className="field"><span>稅別</span><select value={draft.tax_type} onChange={(event) => onChange({ ...draft, tax_type: event.target.value as ProductDraft["tax_type"] })}><option value="taxable">應稅</option><option value="tax_exempt">免稅</option></select></label>
        <label className="field wide"><span>圖片網址</span><input value={draft.image_url} onChange={(event) => onChange({ ...draft, image_url: event.target.value })} /></label>
        <label className="field wide"><span>商品說明</span><textarea rows={4} value={draft.description} onChange={(event) => onChange({ ...draft, description: event.target.value })} /></label>
      </div>
      <div className="admin-option-row"><label className="switch-field"><input type="checkbox" checked={draft.is_active} onChange={(event) => onChange({ ...draft, is_active: event.target.checked })} />上架商品</label><label className="switch-field"><input type="checkbox" checked={draft.can_ship} onChange={(event) => onChange({ ...draft, can_ship: event.target.checked, allowed_shipping_channels: event.target.checked ? draft.allowed_shipping_channels : [] })} />開放配送</label></div>
      {draft.can_ship && <div className="shipping-config"><label className="field"><span>物流溫層</span><select value={draft.shipping_temperature} onChange={(event) => onChange({ ...draft, shipping_temperature: event.target.value as ShippingTemperature })}><option value="ambient">常溫</option><option value="chilled">冷藏</option><option value="frozen">冷凍</option></select></label><div className="channel-checks">{shippingChannels.map((channel) => <label key={channel} className="check-field"><input type="checkbox" checked={draft.allowed_shipping_channels.includes(channel)} onChange={() => toggleChannel(channel)} />{shippingChannelLabels[channel]}</label>)}</div></div>}
      {draft.member_price > draft.nonmember_price && <p className="form-error">社員價不可高於一般價</p>}
      {draft.can_ship && draft.allowed_shipping_channels.length === 0 && <p className="form-error">配送商品至少選擇一個物流通路</p>}
      {error && <p className="form-error">{error}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" onClick={onCancel}>取消</button><button className="button button-primary" disabled={busy || draft.member_price > draft.nonmember_price || (draft.can_ship && draft.allowed_shipping_channels.length === 0)}>儲存商品</button></div>
    </form>
  );
}

function productPayload(draft: ProductDraft) {
  return { ...draft, product_number: draft.product_number || null, sku: draft.sku || null, supplier_id: draft.supplier_id || null, image_url: draft.image_url || null, shipping_temperature: draft.can_ship ? draft.shipping_temperature : null, allowed_shipping_channels: draft.can_ship ? draft.allowed_shipping_channels : [] };
}

function SupplierCreateForm({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<SupplierDraft>(emptySupplier);
  const create = useMutation({ mutationFn: () => apiFetch<Supplier>("/v1/admin/suppliers", { method: "POST", body: JSON.stringify(supplierPayload(draft)) }), onSuccess: () => { setDraft(emptySupplier); setOpen(false); onDone(); } });
  return <section className="admin-create-block"><button className="button button-system" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />新增供應者</button>{open && <SupplierForm title="建立供應者主檔" draft={draft} busy={create.isPending} error={create.error?.message} onChange={setDraft} onCancel={() => setOpen(false)} onSubmit={() => create.mutate()} />}</section>;
}

function SupplierEditor({ supplier, onDone }: { supplier: Supplier; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [accrediting, setAccrediting] = useState(false);
  const [draft, setDraft] = useState<SupplierDraft>(() => ({ supplier_number: supplier.supplier_number || "", business_name: supplier.business_name, tax_id: supplier.tax_id || "", responsible_person: supplier.responsible_person, contact_person: supplier.contact_person, phone: supplier.phone, email: supplier.email, line_id: supplier.line_id || "", settlement_terms: supplier.settlement_terms, bank_account: supplier.bank_account, is_active: supplier.is_active }));
  const update = useMutation({ mutationFn: () => apiFetch<Supplier>(`/v1/admin/suppliers/${supplier.id}`, { method: "PATCH", body: JSON.stringify(supplierPayload(draft)) }), onSuccess: () => { setEditing(false); onDone(); } });
  if (editing) return <SupplierForm title={supplier.business_name} draft={draft} busy={update.isPending} error={update.error?.message} onChange={setDraft} onCancel={() => setEditing(false)} onSubmit={() => update.mutate()} />;
  return (
    <article className="admin-review-card admin-review-card-expandable">
      <div className="review-card-copy"><span className={`status-chip ${supplier.is_active ? "passed" : ""}`}>{supplier.is_active ? "已啟用" : "待審認"}</span><h3>{supplier.business_name}</h3><p>{supplier.supplier_number || "尚未核發編號"} · {supplier.tax_id || "無統編"}</p><small>{supplier.contact_person} · {supplier.phone} · {supplier.email}</small><div className="supplier-meta"><span>結帳：{supplier.settlement_terms || "未填"}</span><span>審認：{supplier.accredited_on ? formatDate(supplier.accredited_on) : "尚未"}</span><span>文件：{supplier.documents.length} 份</span></div></div>
      <div className="review-card-actions"><div><button type="button" onClick={() => setEditing(true)}>編輯資料</button><button type="button" onClick={() => setAccrediting((value) => !value)}><SealCheck size={16} />新增審認</button></div></div>
      {accrediting && <AccreditationForm supplier={supplier} onDone={() => { setAccrediting(false); onDone(); }} />}
      {supplier.accreditations.length > 0 && <div className="supplier-accreditation-list">{supplier.accreditations.map((item) => <p key={item.id}><strong>{formatDate(item.reviewed_on)} · {accreditationLabel(item.status)}</strong><span>{item.result_notes || item.process_notes}</span></p>)}</div>}
    </article>
  );
}

function SupplierForm({ title, draft, busy, error, onChange, onCancel, onSubmit }: { title: string; draft: SupplierDraft; busy: boolean; error?: string; onChange: (draft: SupplierDraft) => void; onCancel: () => void; onSubmit: () => void }) {
  const submit = (event: FormEvent) => { event.preventDefault(); onSubmit(); };
  return (
    <form className="social-form admin-master-form" onSubmit={submit}>
      <div className="form-heading"><div><p className="eyebrow">SUPPLIER RECORD</p><h2>{title}</h2></div><span>聯絡人與銀行資料屬私密資料。</span></div>
      <div className="field-grid three-columns">
        <label className="field"><span>供應商編號</span><input value={draft.supplier_number} onChange={(event) => onChange({ ...draft, supplier_number: event.target.value })} /></label>
        <label className="field"><span>商號</span><input required value={draft.business_name} onChange={(event) => onChange({ ...draft, business_name: event.target.value })} /></label>
        <label className="field"><span>統一編號</span><input inputMode="numeric" pattern="[0-9]{8}" value={draft.tax_id} onChange={(event) => onChange({ ...draft, tax_id: event.target.value })} /></label>
        <label className="field"><span>負責人</span><input required value={draft.responsible_person} onChange={(event) => onChange({ ...draft, responsible_person: event.target.value })} /></label>
        <label className="field"><span>業務接洽人</span><input required value={draft.contact_person} onChange={(event) => onChange({ ...draft, contact_person: event.target.value })} /></label>
        <label className="field"><span>電話</span><input required minLength={8} value={draft.phone} onChange={(event) => onChange({ ...draft, phone: event.target.value })} /></label>
        <label className="field"><span>Email</span><input required type="email" value={draft.email} onChange={(event) => onChange({ ...draft, email: event.target.value })} /></label>
        <label className="field"><span>LINE</span><input value={draft.line_id} onChange={(event) => onChange({ ...draft, line_id: event.target.value })} /></label>
        <label className="field"><span>銀行帳號</span><input required minLength={4} value={draft.bank_account} onChange={(event) => onChange({ ...draft, bank_account: event.target.value })} /></label>
        <label className="field wide"><span>結帳規律</span><textarea rows={3} value={draft.settlement_terms} onChange={(event) => onChange({ ...draft, settlement_terms: event.target.value })} /></label>
      </div>
      <label className="switch-field"><input type="checkbox" checked={draft.is_active} onChange={(event) => onChange({ ...draft, is_active: event.target.checked })} />啟用供應者</label>
      {error && <p className="form-error">{error}</p>}
      <div className="form-actions"><button className="button button-quiet" type="button" onClick={onCancel}>取消</button><button className="button button-primary" disabled={busy}>儲存供應者</button></div>
    </form>
  );
}

function supplierPayload(draft: SupplierDraft) {
  return { ...draft, supplier_number: draft.supplier_number || null, tax_id: draft.tax_id || null, line_id: draft.line_id || null };
}

function AccreditationForm({ supplier, onDone }: { supplier: Supplier; onDone: () => void }) {
  const [reviewedOn, setReviewedOn] = useState(new Date().toISOString().slice(0, 10));
  const [status, setStatus] = useState<SupplierAccreditationStatus>("approved");
  const [processNotes, setProcessNotes] = useState("");
  const [resultNotes, setResultNotes] = useState("");
  const save = useMutation({ mutationFn: () => apiFetch<Supplier>(`/v1/admin/suppliers/${supplier.id}/accreditations`, { method: "POST", body: JSON.stringify({ reviewed_on: reviewedOn, status, process_notes: processNotes, result_notes: resultNotes }) }), onSuccess: onDone });
  return <form className="embedded-admin-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}><h4>供應者審認紀錄</h4><div className="field-grid three-columns"><label className="field"><span>審認日期</span><input type="date" value={reviewedOn} onChange={(event) => setReviewedOn(event.target.value)} /></label><label className="field"><span>結果</span><select value={status} onChange={(event) => setStatus(event.target.value as SupplierAccreditationStatus)}><option value="approved">通過</option><option value="rejected">不通過</option><option value="pending">待確認</option></select></label><label className="field wide"><span>審認過程</span><textarea required rows={3} value={processNotes} onChange={(event) => setProcessNotes(event.target.value)} /></label><label className="field wide"><span>結果說明</span><textarea rows={3} value={resultNotes} onChange={(event) => setResultNotes(event.target.value)} /></label></div>{save.isError && <p className="form-error">{save.error.message}</p>}<button className="button button-primary" disabled={save.isPending}>儲存審認</button></form>;
}

function LocationCreateForm({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ code: "", name: "", address: "", instructions: "", sort_order: 0, is_active: true });
  const create = useMutation({ mutationFn: () => apiFetch<PickupLocation>("/v1/admin/pickup-locations", { method: "POST", body: JSON.stringify(draft) }), onSuccess: () => { setDraft({ code: "", name: "", address: "", instructions: "", sort_order: 0, is_active: true }); setOpen(false); onDone(); } });
  return <section className="admin-create-block"><button className="button button-system" type="button" onClick={() => setOpen((value) => !value)}><Plus size={17} />新增取貨點</button>{open && <LocationForm title="建立取貨點" draft={draft} busy={create.isPending} error={create.error?.message} onChange={setDraft} onCancel={() => setOpen(false)} onSubmit={() => create.mutate()} />}</section>;
}

function LocationEditor({ location, onDone }: { location: PickupLocation; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ code: location.code, name: location.name, address: location.address, instructions: location.instructions, sort_order: location.sort_order, is_active: location.is_active });
  const update = useMutation({ mutationFn: () => apiFetch<PickupLocation>(`/v1/admin/pickup-locations/${location.id}`, { method: "PATCH", body: JSON.stringify(draft) }), onSuccess: () => { setEditing(false); onDone(); } });
  if (editing) return <LocationForm title={location.name} draft={draft} busy={update.isPending} error={update.error?.message} onChange={setDraft} onCancel={() => setEditing(false)} onSubmit={() => update.mutate()} />;
  return <article className="admin-record-card"><MapPin size={22} weight="light" /><span className={`status-chip ${location.is_active ? "passed" : ""}`}>{location.is_active ? "啟用" : "停用"}</span><h3>{location.name}</h3><p>{location.address || "詳細位置待合作社確認"}</p><small>{location.code} · 排序 {location.sort_order}</small><button type="button" onClick={() => setEditing(true)}>編輯取貨點</button></article>;
}

function LocationForm({ title, draft, busy, error, onChange, onCancel, onSubmit }: { title: string; draft: { code: string; name: string; address: string; instructions: string; sort_order: number; is_active: boolean }; busy: boolean; error?: string; onChange: (draft: { code: string; name: string; address: string; instructions: string; sort_order: number; is_active: boolean }) => void; onCancel: () => void; onSubmit: () => void }) {
  return <form className="social-form admin-master-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}><div className="form-heading"><div><p className="eyebrow">PICKUP LOCATION</p><h2>{title}</h2></div></div><div className="field-grid two-columns"><label className="field"><span>代碼</span><input required pattern="[a-z0-9-]+" value={draft.code} onChange={(event) => onChange({ ...draft, code: event.target.value })} /></label><label className="field"><span>名稱</span><input required value={draft.name} onChange={(event) => onChange({ ...draft, name: event.target.value })} /></label><label className="field wide"><span>地址</span><input value={draft.address} onChange={(event) => onChange({ ...draft, address: event.target.value })} /></label><label className="field wide"><span>取貨說明</span><textarea rows={3} value={draft.instructions} onChange={(event) => onChange({ ...draft, instructions: event.target.value })} /></label><label className="field"><span>排序</span><input min={0} type="number" value={draft.sort_order} onChange={(event) => onChange({ ...draft, sort_order: Number(event.target.value) })} /></label></div><label className="switch-field"><input type="checkbox" checked={draft.is_active} onChange={(event) => onChange({ ...draft, is_active: event.target.checked })} />啟用取貨點</label>{error && <p className="form-error">{error}</p>}<div className="form-actions"><button className="button button-quiet" type="button" onClick={onCancel}>取消</button><button className="button button-primary" disabled={busy}>儲存取貨點</button></div></form>;
}

function accreditationLabel(status: SupplierAccreditationStatus) {
  return { approved: "通過", rejected: "不通過", pending: "待確認" }[status] || status;
}
