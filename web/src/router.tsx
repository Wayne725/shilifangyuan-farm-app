import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
} from "@tanstack/react-router";
import type { ComponentType } from "react";

import { AppShell } from "./components/AppShell";

const pageModules = import.meta.glob<Record<string, ComponentType>>("./pages/*.tsx");
const page = (path: string, name: string) =>
  lazyRouteComponent(pageModules[`${path}.tsx`], name);

interface OrdersSearch {
  attempt_id?: string;
  order_id?: string;
  result?: string;
  message?: string;
  payment?: string;
  logistics?: string;
}

interface AccountSearch {
  attempt_id?: string;
  membership_charge_id?: string;
  payment?: string;
}

type RegisterIntent = "existing_member" | "nonmember";

interface RegisterSearch {
  intent?: RegisterIntent;
  return_to?: string;
  remember_me?: boolean;
}

interface VerifyEmailSearch {
  email?: string;
  token?: string;
  intent?: RegisterIntent;
}

interface ResetPasswordSearch {
  token?: string;
}

const rootRoute = createRootRoute({
  component: AppShell,
  notFoundComponent: page("./pages/HelpPages", "NotFoundPage"),
});

const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: page("./pages/HomePage", "HomePage"),
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (search: Record<string, unknown>): { return_to?: string } => ({
    return_to: typeof search.return_to === "string" ? search.return_to : undefined,
  }),
  component: page("./pages/LoginPage", "LoginPage"),
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/register",
  validateSearch: (search: Record<string, unknown>): RegisterSearch => ({
    return_to: typeof search.return_to === "string" ? search.return_to : undefined,
    remember_me: search.remember_me === false || search.remember_me === "false" ? false : true,
    intent:
      search.intent === "existing_member" || search.intent === "member"
        ? "existing_member"
        : search.intent === "nonmember" || search.intent === "customer"
          ? "nonmember"
          : undefined,
  }),
  component: page("./pages/RegistrationPage", "RegisterPage"),
});

const verifyEmailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/verify-email",
  validateSearch: (search: Record<string, unknown>): VerifyEmailSearch => ({
    email: typeof search.email === "string" ? search.email : undefined,
    token: typeof search.token === "string" ? search.token : undefined,
    intent:
      search.intent === "existing_member" || search.intent === "member"
        ? "existing_member"
        : search.intent === "nonmember" || search.intent === "customer"
          ? "nonmember"
          : undefined,
  }),
  component: page("./pages/AuthFlowPages", "VerifyEmailPage"),
});

const forgotPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/forgot-password",
  component: page("./pages/AuthFlowPages", "ForgotPasswordPage"),
});

const resetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reset-password",
  validateSearch: (search: Record<string, unknown>): ResetPasswordSearch => ({
    token: typeof search.token === "string" ? search.token : undefined,
  }),
  component: page("./pages/AuthFlowPages", "ResetPasswordPage"),
});

const shopRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/shop",
  component: page("./pages/ShopPage", "ShopPage"),
});

const helpRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/help",
  component: page("./pages/HelpPages", "HelpPage"),
});

const socialRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/social",
  component: page("./pages/SocialPage", "SocialPage"),
});

const activitiesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/activities",
  component: page("./pages/ActivitiesPage", "ActivitiesPage"),
});

const governanceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/governance",
  component: page("./pages/GovernancePage", "GovernancePage"),
});

const meetingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/meetings",
  component: page("./pages/MeetingsPage", "MeetingsPage"),
});

const proposalDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/proposals/$proposalId",
  component: page("./pages/ProposalDetailPage", "ProposalDetailPage"),
});

const wishesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/wishes",
  component: page("./pages/WishesPage", "WishesPage"),
});

const directoryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/directory",
  component: page("./pages/DirectoryPage", "DirectoryPage"),
});

const membershipRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/membership",
  component: page("./pages/MembershipApplicationPage", "MembershipApplicationPage"),
});

const checkoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/checkout",
  component: page("./pages/CheckoutPage", "CheckoutPage"),
});

const legacyCartRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/cart",
  beforeLoad: () => {
    throw redirect({ to: "/checkout", replace: true });
  },
});

const ordersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/orders",
  validateSearch: (search: Record<string, unknown>): OrdersSearch => ({
    attempt_id:
      typeof search.attempt_id === "string" ? search.attempt_id : undefined,
    order_id: typeof search.order_id === "string" ? search.order_id : undefined,
    result: typeof search.result === "string" ? search.result : undefined,
    message: typeof search.message === "string" ? search.message : undefined,
    payment: typeof search.payment === "string" ? search.payment : undefined,
    logistics: typeof search.logistics === "string" ? search.logistics : undefined,
  }),
  component: page("./pages/OrdersPage", "OrdersPage"),
});

const groupCampaignRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/groups/$campaignId",
  component: page("./pages/GroupCampaignPage", "GroupCampaignPage"),
});

const mealEventRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/meals/$eventId",
  validateSearch: (search: Record<string, unknown>): { pickup_at?: string } => ({
    pickup_at: typeof search.pickup_at === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(search.pickup_at) ? search.pickup_at : undefined,
  }),
  component: page("./pages/MealEventPage", "MealEventPage"),
});

const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  component: page("./pages/AdminPage", "AdminPage"),
  validateSearch: (search: Record<string, unknown>): { q?: string; section?: "orders" | "issues" | "suppliers" | "locations" } => ({
    q: typeof search.q === "string" ? search.q.trim().slice(0, 100) : undefined,
    section: search.section === "issues" || search.section === "suppliers" || search.section === "locations" ? search.section : "orders",
  }),
});

const adminSocialRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/social",
  component: page("./pages/AdminSocialPage", "AdminSocialPage"),
});

const accountRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/account",
  validateSearch: (search: Record<string, unknown>): AccountSearch => ({
    attempt_id:
      typeof search.attempt_id === "string" ? search.attempt_id : undefined,
    membership_charge_id:
      typeof search.membership_charge_id === "string"
        ? search.membership_charge_id
        : undefined,
    payment: typeof search.payment === "string" ? search.payment : undefined,
  }),
  component: page("./pages/AccountPage", "AccountPage"),
});

const productDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/products/$productId",
  component: page("./pages/ProductDetailPage", "ProductDetailPage"),
});

const groupVotesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/group-votes",
  component: page("./pages/GroupVotePages", "GroupVotesPage"),
});

const groupVoteDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/group-votes/$proposalId",
  component: page("./pages/GroupVotePages", "GroupVoteDetailPage"),
});

const newGroupVoteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/group-votes/new",
  component: page("./pages/GroupVotePages", "NewGroupVotePage"),
});

const mealOrdersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/meal-orders",
  validateSearch: (search: Record<string, unknown>): { order_id?: string } => ({
    order_id: typeof search.order_id === "string" ? search.order_id : undefined,
  }),
  component: page("./pages/MealOrdersPage", "MealOrdersPage"),
});

const adminCatalogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/catalog",
  validateSearch: (search: Record<string, unknown>): { tab?: "products" | "suppliers" | "locations" } => ({
    tab: search.tab === "suppliers" || search.tab === "locations" ? search.tab : "products",
  }),
  component: page("./pages/AdminCatalogPage", "AdminCatalogPage"),
});

const adminGroupsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/groups",
  component: page("./pages/AdminGroupsPage", "AdminGroupsPage"),
});

const adminMealsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/meals",
  validateSearch: (search: Record<string, unknown>): { tab?: string; q?: string } => ({
    tab: typeof search.tab === "string" && ["orders", "cancellations", "events", "schedules", "meals"].includes(search.tab) ? search.tab : undefined,
    q: typeof search.q === "string" ? search.q.slice(0, 100) : undefined,
  }),
  component: page("./pages/AdminMealsPage", "AdminMealsPage"),
});

const adminFinanceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/finance",
  component: page("./pages/AdminFinancePage", "AdminFinancePage"),
});

const adminKitchenRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/kitchen",
  component: page("./pages/AdminKitchenPage", "AdminKitchenPage"),
});

const adminRecordsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/records", component: page("./pages/AdminRecordsPage", "AdminRecordsPage") });

const adminPromotionsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/promotions", component: page("./pages/AdminPromotionsPage", "AdminPromotionsPage") });

const adminInvoicesRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/invoices", component: page("./pages/AdminInvoicesPage", "AdminInvoicesPage") });
const adminStatementsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/statements", component: page("./pages/AdminStatementsPage", "AdminStatementsPage") });

const adminSettingsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/settings", component: page("./pages/AdminSettingsPage", "AdminSettingsPage") });

const adminUsersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/users",
  component: page("./pages/AdminUsersPage", "AdminUsersPage"),
});

const lineLoginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/auth/line/callback",
  validateSearch: (search: Record<string, unknown>): { code?: string; state?: string; error?: string } => ({
    code: typeof search.code === "string" ? search.code : undefined,
    state: typeof search.state === "string" ? search.state : undefined,
    error: typeof search.error === "string" ? search.error : undefined,
  }),
  component: page("./pages/LineLoginPage", "LineLoginPage"),
});

const lineConsentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/line-consent",
  validateSearch: (search: Record<string, unknown>): { return_to?: string; remember_me?: boolean } => ({
    return_to: typeof search.return_to === "string" ? search.return_to : undefined,
    remember_me: search.remember_me === false || search.remember_me === "false" ? false : true,
  }),
  component: page("./pages/LineConsentPage", "LineConsentPage"),
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  lineLoginRoute,
  lineConsentRoute,
  homeRoute,
  registerRoute,
  verifyEmailRoute,
  forgotPasswordRoute,
  resetPasswordRoute,
  shopRoute,
  helpRoute,
  productDetailRoute,
  socialRoute,
  activitiesRoute,
  governanceRoute,
  meetingsRoute,
  proposalDetailRoute,
  wishesRoute,
  directoryRoute,
  membershipRoute,
  checkoutRoute,
  legacyCartRoute,
  ordersRoute,
  mealOrdersRoute,
  groupCampaignRoute,
  groupVotesRoute,
  groupVoteDetailRoute,
  newGroupVoteRoute,
  mealEventRoute,
  adminRoute,
  adminCatalogRoute,
  adminGroupsRoute,
  adminMealsRoute,
  adminFinanceRoute,
  adminKitchenRoute,
  adminUsersRoute,
  adminSettingsRoute,
  adminInvoicesRoute,
  adminStatementsRoute,
  adminPromotionsRoute,
  adminRecordsRoute,
  adminSocialRoute,
  accountRoute,
]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  scrollRestoration: true,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
