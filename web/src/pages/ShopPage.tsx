import { StorefrontClosed, useStorefrontStatus } from "../components/StorefrontStatus";
import * as Tabs from "@radix-ui/react-tabs";
import { ArrowRight, BowlFood, CalendarBlank, Package, UsersThree } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { DataState, LoadingLines, ProductCard, SectionHeading } from "../components/Shared";
import { MealDateNavigation } from "../components/MealDateNavigation";
import { useCommerce } from "../context/CommerceContext";
import { apiFetch, formatDate } from "../lib/api";
import { campaignOrderBlockReason, campaignStatusLabel } from "../lib/group-campaigns";
import type { GroupCampaign, MealEvent, Product } from "../lib/types";

export function ShopPage() {
  const platform = useStorefrontStatus();
  const { addToCart, itemCount } = useCommerce();
  const [activeTab, setActiveTab] = useState("meals");
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
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
    refetchInterval: 60000,
  });
  const upcomingMeals = meals.data?.filter((meal) =>
    !["draft", "cancelled", "completed"].includes(meal.status)
      && new Date(meal.pickup_ends_at).getTime() > now,
  ) || [];
  const pageTitle = activeTab === "meals" ? "我要訂餐" : activeTab === "groups" ? "共同購買" : "日常選品";

  if (platform.data?.orders_open === false) return <StorefrontClosed message={platform.data.closed_message} />;
  return (
    <>
      <section className="page-intro shop-intro meal-shop-intro">
        <div>
          <h1>{pageTitle}</h1>
        </div>
        {activeTab === "meals" && <nav className="meal-shop-actions" aria-label="便當常用功能"><Link className="button button-quiet" to="/meal-orders">我的便當訂單</Link></nav>}
      </section>

      <Tabs.Root className="market-tabs" value={activeTab} onValueChange={setActiveTab}>
        <div className="tab-list market-navigation">
          <Tabs.List className="market-type-tabs" aria-label="消費類型">
            <Tabs.Trigger value="meals">
              <BowlFood size={18} weight="light" />
              便當預購
            </Tabs.Trigger>
            <Tabs.Trigger value="groups">
              <UsersThree size={18} weight="light" />
              共同購買
            </Tabs.Trigger>
            <Tabs.Trigger value="products">
              <Package size={18} weight="light" />
              日常選品
            </Tabs.Trigger>
          </Tabs.List>
          <Link to="/activities">
            <CalendarBlank size={18} weight="light" />
            永續活動
          </Link>
        </div>

        <Tabs.Content value="products" className="tab-content">
          <SectionHeading
            eyebrow="CURATED PROVISIONS / 合作社選品"
            title="合作社選品"
          />
          {products.isPending && <LoadingLines count={4} />}
          {products.isError && (
            <DataState
              kind="error"
              title="商品服務暫時無法使用"
              detail={products.error.message}
              onAction={() => products.refetch()}
            />
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
            eyebrow="BUYING TOGETHER / 共同購買"
            title="把需求聚在一起"
          />
          <div className="market-subnav"><Link className="button button-quiet" to="/group-votes"><UsersThree size={17} />查看需求投票</Link><Link className="text-link" to="/group-votes/new">發起新的共同購買需求 <ArrowRight size={17} /></Link></div>
          {campaigns.isPending && <LoadingLines count={3} />}
          {campaigns.isError && (
            <DataState
              kind="error"
              title="共同購買資料暫時不可用"
              detail={campaigns.error.message}
              onAction={() => campaigns.refetch()}
            />
          )}
          <div className="editorial-list">
            {campaigns.data?.map((campaign, index) => (
              <article key={campaign.id} className="editorial-card">
                <div className="editorial-number">{String(index + 1).padStart(2, "0")}</div>
                <div>
                  <span>{campaignStatusLabel(campaign, now)}</span>
                  <h3>{campaign.title || "共同採購"}</h3>
                  <p>{campaign.description || "供應與達標資訊以系統紀錄為準。"}</p>
                </div>
                <Link className="editorial-end" to="/groups/$campaignId" params={{ campaignId: campaign.id }}>
                  <small>截止 {formatDate(campaign.deadline)}</small>
                  <span>{campaignOrderBlockReason(campaign, now) ? "查看共同購買" : "查看並參加"} <ArrowRight size={22} weight="light" /></span>
                </Link>
              </article>
            ))}
          </div>
        </Tabs.Content>

        <Tabs.Content value="meals" className="tab-content meal-booking-content">
          <SectionHeading
            eyebrow="MEAL PREORDER / 便當預訂"
            title="選取餐時間"
          />
          {meals.isSuccess && <MealDateNavigation events={upcomingMeals} now={now} />}
          {meals.isPending && <LoadingLines count={2} />}
          {meals.isError && (
            <DataState
              kind="error"
              title="餐期資料暫時不可用"
              detail={meals.error.message}
              onAction={() => meals.refetch()}
            />
          )}
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
