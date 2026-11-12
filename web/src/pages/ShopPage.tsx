import { StorefrontClosed, useStorefrontStatus } from "../components/StorefrontStatus";
import * as Tabs from "@radix-ui/react-tabs";
import { ArrowRight, BowlFood, CalendarBlank, Package, UsersThree } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { DataState, LoadingLines, ProductCard, SectionHeading } from "../components/Shared";
import { MealDateNavigation } from "../components/MealDateNavigation";
import { useCommerce } from "../context/CommerceContext";
import { ApiError, apiFetch, formatDate } from "../lib/api";
import { campaignOrderBlockReason, campaignStatusLabel } from "../lib/group-campaigns";
import { mealMenuAvailability } from "../lib/meal-time";
import type { GroupCampaign, MealCalendarEvent, MealEvent, Product } from "../lib/types";
import "./ShopPage.css";

const productsPerPage = 12;

function normalizeProductSearch(value: string) {
  return value.normalize("NFKC").toLocaleLowerCase("zh-TW");
}

export function ShopPage() {
  const platform = useStorefrontStatus();
  const { addToCart, itemCount } = useCommerce();
  const { tab, q, category, page } = useSearch({ from: "/shop" });
  const navigate = useNavigate({ from: "/shop" });
  const activeTab = tab || "meals";
  const [productQuery, setProductQuery] = useState(q ?? "");
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const products = useQuery({
    queryKey: ["products"],
    queryFn: () => apiFetch<Product[]>("/v1/products"),
  });
  const dailyProducts = products.data?.filter((item) => item.is_active !== false && item.daily_sales_enabled !== false) ?? [];
  const categories = [...new Set(dailyProducts.map((product) => product.category).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-TW"));
  const query = normalizeProductSearch(q ?? "");
  const matches = dailyProducts.filter((product) => {
    if (category && product.category !== category) return false;
    return !query || [product.name, product.description, product.category]
      .some((value) => normalizeProductSearch(value ?? "").includes(query));
  });
  const pageCount = Math.max(1, Math.ceil(matches.length / productsPerPage));
  const currentPage = Math.min(page ?? 1, pageCount);
  const currentProducts = matches.slice((currentPage - 1) * productsPerPage, currentPage * productsPerPage);
  useEffect(() => {
    setProductQuery(q ?? "");
  }, [q, category, page]);
  useEffect(() => {
    if (activeTab === "products" && products.isSuccess && !products.isFetching && page && page > pageCount) {
      void navigate({
        search: (previous) => ({ ...previous, page: pageCount > 1 ? pageCount : undefined }),
        replace: true,
        resetScroll: false,
      });
    }
  }, [activeTab, navigate, page, pageCount, products.isFetching, products.isSuccess]);
  const updateProductFilters = (nextQuery: string, nextCategory?: string) => {
    const trimmedQuery = nextQuery.trim();
    setProductQuery(trimmedQuery);
    void navigate({
      search: { tab: "products", q: trimmedQuery || undefined, category: nextCategory || undefined },
      resetScroll: false,
    });
  };
  const campaigns = useQuery({
    queryKey: ["group-campaigns"],
    queryFn: () => apiFetch<GroupCampaign[]>("/v1/group-campaigns"),
  });
  const meals = useQuery({
    queryKey: ["meal-events", "calendar"],
    queryFn: async ({ signal }) => {
      try {
        return await apiFetch<MealCalendarEvent[]>("/v1/meal-events/calendar", { signal });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error;
        const events = await apiFetch<MealEvent[]>("/v1/meal-events", { signal });
        return events.map(({ offerings, ...timing }) => ({ ...timing, ...mealMenuAvailability(offerings) }));
      }
    },
    enabled: activeTab === "meals",
    refetchInterval: 60_000,
  });
  const announcements = useQuery({
    queryKey: ["meal-announcements"],
    queryFn: ({ signal }) => apiFetch<Array<{ service_date: string; periods: string[]; message: string }>>("/v1/meal-announcements", { signal }),
    enabled: activeTab === "meals",
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

      {activeTab === "meals" && Boolean(announcements.data?.length) && <section className="meal-shop-actions" aria-label="供餐公告">
        {announcements.data?.map((notice, index) => <p key={`${notice.service_date}-${index}`} role="status">
          <strong>{notice.service_date} {notice.periods.includes("lunch") ? "午餐" : ""}{notice.periods.includes("dinner") ? " 晚餐" : ""}</strong> · {notice.message}
        </p>)}
      </section>}
      <Tabs.Root className="market-tabs" value={activeTab} onValueChange={(next) => {
        if (next === "meals" || next === "groups" || next === "products") {
          void navigate({ search: (previous) => ({ ...previous, tab: next }), resetScroll: false });
        }
      }}>
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
          {products.isSuccess && dailyProducts.length === 0 && (
            <DataState title="商品整理中" detail="目前沒有可選購的商品。" />
          )}
          {dailyProducts.length > 0 && (
            <>
              <form className="shop-product-filters" role="search" aria-label="日常選品" onSubmit={(event) => {
                event.preventDefault();
                updateProductFilters(productQuery, category);
              }}>
                <label className="field">
                  <span>搜尋商品</span>
                  <input type="search" maxLength={120} value={productQuery} onChange={(event) => setProductQuery(event.target.value)} />
                </label>
                <label className="field">
                  <span>商品分類</span>
                  <select value={category ?? ""} onChange={(event) => updateProductFilters(productQuery, event.target.value)}>
                    <option value="">全部分類</option>
                    {categories.map((name) => <option key={name} value={name}>{name}</option>)}
                    {category && !categories.includes(category) && <option value={category}>未上架的分類</option>}
                  </select>
                </label>
                <div className="shop-product-filter-actions">
                  <button className="button button-primary" type="submit">搜尋</button>
                  {(q || category) && (
                    <button className="button button-quiet" type="button" onClick={() => updateProductFilters("")}>清除篩選</button>
                  )}
                </div>
              </form>
              <div className="shop-product-results">
                <p role="status">{matches.length ? `共 ${matches.length} 件商品` : "找不到符合的商品"}</p>
                {pageCount > 1 && (
                  <nav className="shop-product-pagination" aria-label="商品分頁">
                    {currentPage > 1 ? (
                      <Link className="button button-quiet" to="/shop"
                        search={{ tab: "products", q, category, page: currentPage > 2 ? currentPage - 1 : undefined }}
                        resetScroll={false}>上一頁</Link>
                    ) : <button className="button button-quiet" type="button" disabled>上一頁</button>}
                    <span>第 {currentPage} / {pageCount} 頁</span>
                    {currentPage < pageCount ? (
                      <Link className="button button-quiet" to="/shop"
                        search={{ tab: "products", q, category, page: currentPage + 1 }}
                        resetScroll={false}>下一頁</Link>
                    ) : <button className="button button-quiet" type="button" disabled>下一頁</button>}
                  </nav>
                )}
              </div>
              {currentProducts.length > 0 && (
                <div className="product-grid catalog-grid">
                  {currentProducts.map((product) => (
                    <ProductCard key={product.id} product={product} onAdd={addToCart} />
                  ))}
                </div>
              )}
            </>
          )}
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
