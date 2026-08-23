import * as Tabs from "@radix-ui/react-tabs";
import { ArrowRight, BowlFood, Package, UsersThree } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { DataState, LoadingLines, ProductCard, SectionHeading } from "../components/Shared";
import { useCommerce } from "../context/CommerceContext";
import { apiFetch, formatDate, resolveAsset } from "../lib/api";
import { mealEventStatusLabel } from "../lib/labels";
import type { GroupCampaign, MealEvent, Product } from "../lib/types";

export function ShopPage() {
  const { addToCart, itemCount } = useCommerce();
  const products = useQuery({
    queryKey: ["products"],
    queryFn: () => apiFetch<Product[]>("/v1/products"),
  });
  const campaigns = useQuery({
    queryKey: ["group-campaigns"],
    queryFn: () => apiFetch<GroupCampaign[]>("/v1/group-campaigns"),
  });
  const meals = useQuery({
    queryKey: ["meal-events"],
    queryFn: () => apiFetch<MealEvent[]>("/v1/meal-events"),
  });

  return (
    <>
      <section className="page-intro shop-intro">
        <div>
          <p className="eyebrow">LIVING MARKET / 生活消費</p>
          <h1>每一次選擇，<br /><em>都知道從哪裡來。</em></h1>
        </div>
        <div className="intro-aside">
          <p>
            REGULAR GOODS · GROUP ORDERS · MEAL PRE-ORDER
          </p>
          <span>商品編號、SKU、供應者與稅別皆由合作社後台維護。</span>
        </div>
      </section>

      <Tabs.Root className="market-tabs" defaultValue="products">
        <Tabs.List className="tab-list" aria-label="消費類型">
          <Tabs.Trigger value="products">
            <Package size={18} weight="light" />
            日常選品
          </Tabs.Trigger>
          <Tabs.Trigger value="groups">
            <UsersThree size={18} weight="light" />
            共同團購
          </Tabs.Trigger>
          <Tabs.Trigger value="meals">
            <BowlFood size={18} weight="light" />
            便當預購
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="products" className="tab-content">
          <SectionHeading
            eyebrow="CURATED PROVISIONS"
            title="合作社選品"
            description="價格、庫存與供應批次會隨當期上架內容更新。"
          />
          {products.isPending && <LoadingLines count={4} />}
          {products.isError && (
            <DataState kind="error" title="商品服務暫時無法使用" detail="請稍後重新整理。" />
          )}
          {products.data?.length === 0 && (
            <DataState title="商品整理中" detail="完成正式商品與 SKU 建檔後，選品會出現在這裡。" />
          )}
          <div className="product-grid catalog-grid">
            {products.data?.map((product) => (
              <ProductCard key={product.id} product={product} onAdd={addToCart} />
            ))}
          </div>
        </Tabs.Content>

        <Tabs.Content value="groups" className="tab-content">
          <SectionHeading
            eyebrow="BUYING TOGETHER"
            title="把需求聚在一起"
            description="集結社員需求，依成團數量與供應上限共同採購。"
          />
          <div className="market-subnav"><Link className="button button-quiet" to="/group-votes"><UsersThree size={17} />查看需求投票</Link><Link className="text-link" to="/group-votes/new">發起新的團購需求 <ArrowRight size={17} /></Link></div>
          {campaigns.isPending && <LoadingLines count={3} />}
          {campaigns.isError && (
            <DataState kind="error" title="團購資料暫時不可用" detail="登入社員帳號後可查看符合權限的團購。" />
          )}
          <div className="editorial-list">
            {campaigns.data?.map((campaign, index) => (
              <article key={campaign.id} className="editorial-card">
                <div className="editorial-number">{String(index + 1).padStart(2, "0")}</div>
                <div>
                  <span>{campaign.intake_status || "進行中"}</span>
                  <h3>{campaign.title || "共同採購"}</h3>
                  <p>{campaign.description || "供應與達標資訊以系統紀錄為準。"}</p>
                </div>
                <Link className="editorial-end" to="/groups/$campaignId" params={{ campaignId: campaign.id }}>
                  <small>截止 {formatDate(campaign.deadline)}</small>
                  <span>查看並參加 <ArrowRight size={22} weight="light" /></span>
                </Link>
              </article>
            ))}
          </div>
        </Tabs.Content>

        <Tabs.Content value="meals" className="tab-content">
          <SectionHeading
            eyebrow="MEALS ON SCHEDULE"
            title="預約一份剛好的午餐"
            description="可訂數量、截止時間與領餐窗口直接跟著餐期走。"
          />
          <div className="market-subnav"><Link className="button button-quiet" to="/meal-orders"><BowlFood size={17} />我的取餐憑證</Link></div>
          {meals.isPending && <LoadingLines count={2} />}
          {meals.isError && (
            <DataState kind="error" title="餐期資料暫時不可用" detail="請稍後再試。" />
          )}
          <div className="meal-grid">
            {meals.data?.map((meal) => (
              <Link key={meal.id} className="meal-card" to="/meals/$eventId" params={{ eventId: meal.id }}>
                <img src={resolveAsset(meal.offerings[0]?.image_url || "/assets/meals/taiwanese-lunchbox.webp")} alt={meal.title} />
                <div>
                  <span>{mealEventStatusLabel(meal.status)}</span>
                  <h3>{meal.title}</h3>
                  <p>{meal.offerings.map((offering) => offering.meal_name).join("、") || "供餐內容與取餐時段由合作社公告。"}</p>
                  <small>預訂截止 {formatDate(meal.ordering_ends_at)}</small>
                </div>
              </Link>
            ))}
          </div>
        </Tabs.Content>
      </Tabs.Root>

      {itemCount > 0 && (
        <Link className="floating-cart" to="/checkout">
          <span>{itemCount}</span>
          查看結帳清單
          <ArrowRight size={18} weight="light" />
        </Link>
      )}
    </>
  );
}
