import {
  Basket,
  Buildings,
  List,
  Receipt,
  SignIn,
  SignOut,
  Storefront,
  UserCircle,
  X,
} from "@phosphor-icons/react";
import { Link, Outlet } from "@tanstack/react-router";
import { useState } from "react";

import { useAuth } from "../context/AuthContext";
import { useCommerce } from "../context/CommerceContext";

const navItems = [
  { to: "/" as const, label: "首頁" },
  { to: "/shop" as const, label: "生活消費", icon: Storefront },
  { to: "/social" as const, label: "社務系統", icon: Buildings },
];

export function AppShell() {
  const { itemCount } = useCommerce();
  const { user, isAuthReady, openLogin, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="site-frame">
      <a className="skip-link" href="#main-content">跳到主要內容</a>
      <header className="site-header">
        <Link className="brand" to="/" aria-label="十里方圓首頁">
          <img className="brand-logo" src="/brand/logo-transparent.png" alt="十里方圓標誌" />
          <span>
            <strong>十里方圓</strong>
            <small>SHILI FANGYUAN CO-OP</small>
          </span>
        </Link>

        <nav className="desktop-nav" aria-label="主要導覽">
          {navItems.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              activeProps={{ className: "active" }}
              activeOptions={{ exact: to === "/" }}
            >
              {Icon && <Icon size={17} weight="light" />}
              {label}
            </Link>
          ))}
          {user?.user_role === "admin" && (
            <Link to="/admin" activeProps={{ className: "active" }}>
              管理工作台
            </Link>
          )}
        </nav>

        <div className="header-actions">
          {user && (
            <Link className="orders-link" to="/orders" search={{ order_id: undefined, result: undefined, message: undefined }} aria-label="查看我的訂單">
              <Receipt size={20} weight="light" />
              <span>訂單</span>
            </Link>
          )}
          <Link className="cart-link" to="/checkout" aria-label="前往結帳">
            <Basket size={21} weight="light" />
            <span>結帳</span>
            {itemCount > 0 && <b>{itemCount}</b>}
          </Link>
          {user ? (
            <Link className="account-button" to="/account">
              <UserCircle size={21} weight="light" />
              <span>{user.display_name}</span>
            </Link>
          ) : (
            <button
              className="account-button"
              type="button"
              onClick={openLogin}
              disabled={!isAuthReady}
            >
              <SignIn size={20} weight="light" />
              <span>{isAuthReady ? "帳號登入" : "確認登入中…"}</span>
            </button>
          )}
          <button
            className="mobile-menu-button"
            type="button"
            aria-label={mobileOpen ? "關閉導覽" : "開啟導覽"}
            onClick={() => setMobileOpen((value) => !value)}
          >
            {mobileOpen ? <X size={22} /> : <List size={22} />}
          </button>
        </div>
      </header>

      {mobileOpen && (
        <nav className="mobile-nav" aria-label="行動版導覽">
          {navItems.map(({ to, label }) => (
            <Link key={to} to={to} onClick={() => setMobileOpen(false)}>
              {label}
            </Link>
          ))}
          <Link to="/checkout" onClick={() => setMobileOpen(false)}>
            結帳清單 ({itemCount})
          </Link>
          {user && (
            <>
              <Link to="/orders" search={{ order_id: undefined, result: undefined, message: undefined }} onClick={() => setMobileOpen(false)}>
                我的訂單
              </Link>
              <Link to="/account" onClick={() => setMobileOpen(false)}>
                會員中心
              </Link>
            </>
          )}
          {user?.user_role === "admin" && (
            <Link to="/admin" onClick={() => setMobileOpen(false)}>
              管理工作台
            </Link>
          )}
          {user ? (
            <button
              type="button"
              onClick={() => {
                logout();
                setMobileOpen(false);
              }}
            >
              登出 {user.display_name}
            </button>
          ) : (
            <button
              type="button"
              disabled={!isAuthReady}
              onClick={() => {
                openLogin();
                setMobileOpen(false);
              }}
            >
              {isAuthReady ? "帳號登入" : "確認登入中…"}
            </button>
          )}
        </nav>
      )}

      <main id="main-content">
        <Outlet />
      </main>

      <footer className="site-footer">
        <div>
          <img className="brand-logo small" src="/brand/logo-transparent.png" alt="十里方圓標誌" />
          <p>把消費、互助與共同治理，放回同一個日常入口。</p>
        </div>
        <div className="footer-meta">
          <Link to="/help">使用說明</Link>
          <span>新竹・台灣</span>
          <span>社員共同所有 · 日常共同參與</span>
        </div>
      </footer>
    </div>
  );
}
