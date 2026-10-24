import {
  Basket,
  Buildings,
  CalendarBlank,
  List,
  Receipt,
  SignIn,
  SignOut,
  Storefront,
  UserCircle,
  X,
} from "@phosphor-icons/react";
import { Link, Outlet } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "../context/AuthContext";
import { useCommerce } from "../context/CommerceContext";
import { useSocialAccess } from "../lib/social-access";
import "../styles/site-navigation.css";

const navItems = [
  { to: "/" as const, label: "首頁" },
  { to: "/shop" as const, label: "生活消費", icon: Storefront },
  { to: "/activities" as const, label: "永續活動", icon: CalendarBlank },
  { to: "/social" as const, label: "社務系統", icon: Buildings },
];

export function AppShell() {
  const { itemCount } = useCommerce();
  const { user, isAuthReady, sessionError, retrySession, openLogin, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const readingMenu = useRef<HTMLDetailsElement>(null);
  const socialAccess = useSocialAccess(user);
  const visibleNavItems = navItems.filter((item) => item.to !== "/social" || socialAccess);
  useEffect(() => {
    if (!mobileOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMobileOpen(false);
      menuButton.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileOpen]);
  const [largeText, setLargeText] = useState(() => {
    try { return localStorage.getItem("slf_text_size") === "large"; } catch { return false; }
  });
  useEffect(() => {
    document.documentElement.dataset.textSize = largeText ? "large" : "standard";
    try { localStorage.setItem("slf_text_size", largeText ? "large" : "standard"); } catch { /* The current size still applies when storage is unavailable. */ }
  }, [largeText]);
  const changeTextSize = (large: boolean) => {
    setLargeText(large);
    if (readingMenu.current) {
      readingMenu.current.open = false;
      readingMenu.current.querySelector("summary")?.focus();
    }
  };

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
          {visibleNavItems.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              activeProps={{ className: "active" }}
              activeOptions={{ exact: to === "/" }}
              onClick={() => setMobileOpen(false)}
            >
              {Icon && <Icon size={17} weight="light" />}
              {label}
            </Link>
          ))}
        </nav>

        <div className="header-actions">
          <details className="reading-menu" ref={readingMenu} onKeyDown={(event) => {
            if (event.key === "Escape" && readingMenu.current) {
              readingMenu.current.open = false;
              readingMenu.current.querySelector("summary")?.focus();
            }
          }}>
            <summary aria-label="文字大小"><span aria-hidden="true">Aa</span><span className="reading-menu-label">字級</span></summary>
            <div className="reading-controls" role="group" aria-label="文字大小">
              <button type="button" aria-pressed={!largeText} onClick={() => changeTextSize(false)}>標準</button>
              <button type="button" aria-pressed={largeText} onClick={() => changeTextSize(true)}>放大</button>
            </div>
          </details>
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
            <Link className="account-button" to="/account" aria-label={`會員中心：${user.display_name}`}>
              <UserCircle size={21} weight="light" />
              <span>{user.display_name}</span>
            </Link>
          ) : (
            <button
              className="account-button"
              type="button"
              aria-label={isAuthReady ? "帳號登入" : "確認登入中…"}
              onClick={() => { setMobileOpen(false); openLogin(); }}
              disabled={!isAuthReady}
            >
              <SignIn size={20} weight="light" />
              <span>{isAuthReady ? "帳號登入" : "確認登入中…"}</span>
            </button>
          )}
          <button
            className="mobile-menu-button"
            ref={menuButton}
            type="button"
            aria-label={mobileOpen ? "關閉導覽" : "開啟導覽"}
            aria-expanded={mobileOpen}
            aria-controls="site-menu"
            onClick={() => setMobileOpen((value) => !value)}
          >
            {mobileOpen ? <X size={22} /> : <List size={22} />}
          </button>
        </div>
      </header>

      {mobileOpen && (
        <nav className="mobile-nav site-menu" id="site-menu" aria-label="網站選單">
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
          <Link to="/help" onClick={() => setMobileOpen(false)}>常見問題</Link>
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
        {sessionError ? (
          <section className="content-section" role="alert">
            <h1>暫時無法確認登入狀態</h1>
            <p>{sessionError}</p>
            <p>目前尚未判定為登出，請重試連線，不需要立即重新輸入帳號密碼。</p>
            <button className="button button-primary" type="button" onClick={retrySession}>重試連線</button>
          </section>
        ) : <Outlet />}
      </main>

      <footer className="site-footer">
        <div>
          <img className="brand-logo small" src="/brand/logo-transparent.png" alt="十里方圓標誌" />
          <p>把消費、互助與共同治理，放回同一個日常入口。</p>
        </div>
        <div className="footer-meta">
          <span>新竹・台灣</span>
          <span>社員共同所有 · 日常共同參與</span>
        </div>
      </footer>
    </div>
  );
}
