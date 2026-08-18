import { ArrowDown, ArrowRight, CirclesThreePlus } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { DataState, LoadingLines, ProductCard, SectionHeading } from "../components/Shared";
import { useAuth } from "../context/AuthContext";
import { useCommerce } from "../context/CommerceContext";
import { apiFetch, replaceBrokenAsset, resolveAsset } from "../lib/api";
import type { Activity, Product } from "../lib/types";

export function HomePage() {
  const { addToCart } = useCommerce();
  const { user, openLogin } = useAuth();
  const products = useQuery({
    queryKey: ["products"],
    queryFn: () => apiFetch<Product[]>("/v1/products"),
  });
  const activities = useQuery({
    queryKey: ["activities"],
    queryFn: () => apiFetch<Activity[]>("/v1/activities"),
    enabled: Boolean(user),
  });
  const heroProduct = products.data?.find((item) => item.image_url);

  return (
    <>
      <section className="hero-shell">
        <div className="hero-index" aria-hidden="true">
          <span>01</span>
          <i />
          <small>COOPERATIVE DAILY LIFE</small>
        </div>
        <div className="hero-copy">
          <p className="eyebrow">A CO-OP, HELD IN COMMON</p>
          <h1>
            <span>讓一日三餐，</span>
            <em>成為共同生活。</em>
          </h1>
          <p className="hero-lead">
            從一袋米、一次預購，到一場社員會議。十里方圓把可靠的消費與真正的共同治理，放進同一個入口。
          </p>
          <p className="hero-translation">
            From the food we choose to the decisions we share — one place for
            everyday cooperation.
          </p>
          <div className="hero-actions">
            <Link className="button button-primary" to="/shop">
              進入生活消費
              <ArrowRight size={19} weight="light" />
            </Link>
            <Link className="button button-quiet" to="/social">
              查看本月社務
            </Link>
          </div>
          <a className="scroll-cue" href="#daily-selection">
            <ArrowDown size={17} weight="light" />
            今日共同選擇
          </a>
        </div>
        <div className="hero-visual">
          <div className="hero-image-bezel">
            <img
              src={resolveAsset(heroProduct?.image_url || "/assets/products/rice.jpg")}
              alt={heroProduct?.name || "合作社選品白米"}
              onError={replaceBrokenAsset}
            />
          </div>
          <div className="hero-note">
            <span>本期選品 / CURRENT EDIT</span>
            <strong>{heroProduct?.name || "產地與日常之間"}</strong>
            <p>{heroProduct?.supplier_name || "由合作社共同把關來源與供應批次"}</p>
          </div>
        </div>
      </section>

      <section className="home-principles">
        <div>
          <span>01</span>
          <p>來源可追</p>
          <small>Supplier & batch records</small>
        </div>
        <div>
          <span>02</span>
          <p>社員有價</p>
          <small>Member-aware pricing</small>
        </div>
        <div>
          <span>03</span>
          <p>決策共讀</p>
          <small>Transparent governance</small>
        </div>
      </section>

      <section className="content-section" id="daily-selection">
        <SectionHeading
          eyebrow="DAILY PROVISIONS"
          title="今天，從這裡開始。"
          description="當期商品、即時庫存與社員價格，以結帳時狀態為準。"
          action={{ label: "瀏覽全部選品", to: "/shop" }}
        />
        {products.isPending && <LoadingLines count={3} />}
        {products.isError && (
          <DataState
            kind="error"
            title="目前無法讀取商品"
            detail="商品服務暫時無法使用，請稍後再試。"
          />
        )}
        {products.data && products.data.length === 0 && (
          <DataState title="尚未上架商品" detail="管理員完成商品建檔後會顯示在這裡。" />
        )}
        <div className="product-grid feature-products">
          {products.data?.slice(0, 3).map((product) => (
            <ProductCard key={product.id} product={product} onAdd={addToCart} />
          ))}
        </div>
      </section>

      <section className="workspace-passage">
        <div className="passage-copy">
          <p className="eyebrow">ONE IDENTITY, TWO WORKSPACES</p>
          <h2>不是多一個平台，<br />是把彼此連回來。</h2>
          <p>
            購買紀錄、社員身分、活動參與與共同決策，都集中在同一個合作社帳號。
          </p>
        </div>
        <Link className="workspace-door consumer" to="/shop">
          <span>生活消費</span>
          <strong>選品・團購・便當預購</strong>
          <ArrowRight size={25} weight="light" />
        </Link>
        <Link className="workspace-door society" to="/social">
          <span>社務系統</span>
          <strong>社員・活動・提案與治理</strong>
          <ArrowRight size={25} weight="light" />
        </Link>
      </section>

      <section className="content-section community-preview">
        <SectionHeading
          eyebrow="COMMON AGENDA"
          title="共同生活，正在發生。"
          action={{ label: "進入社務系統", to: "/social" }}
        />
        <div className="agenda-strip">
          {activities.data?.slice(0, 3).map((activity, index) => (
            <article key={activity.id}>
              <span>0{index + 1}</span>
              <div>
                <small>{activityStatusLabel(activity.status)}</small>
                <h3>{activity.title}</h3>
                <p>{activity.description || activity.location || "社員活動資訊"}</p>
              </div>
            </article>
          ))}
          {activities.isPending && user && <LoadingLines count={2} />}
          {!user && (
            <button className="quiet-placeholder quiet-placeholder-button" type="button" onClick={openLogin}>
              <CirclesThreePlus size={26} weight="light" />
              <p>登入正式社員帳號後顯示本月活動。</p>
            </button>
          )}
          {activities.data?.length === 0 && (
            <div className="quiet-placeholder">
              <CirclesThreePlus size={26} weight="light" />
              <p>活動排程尚未公布。</p>
            </div>
          )}
        </div>
      </section>
    </>
  );
}

function activityStatusLabel(status?: string): string {
  return {
    draft: "草稿",
    pending_review: "待審核",
    published: "開放報名",
    rejected: "未通過",
    cancelled: "已取消",
    completed: "已結束",
  }[status || ""] || "活動公告";
}
