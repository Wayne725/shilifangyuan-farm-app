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
  return_to?: string;
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
    return_to: typeof search.return_to === "string" ? search.return_to : undefined,
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
  validateSearch: (search: Record<string, unknown>): {
    tab?: "meals" | "groups" | "products";
    q?: string;
    category?: string;
    page?: number;
  } => ({
    tab: search.tab === "meals" || search.tab === "groups" || search.tab === "products" ? search.tab : undefined,
    q: typeof search.q === "string" || typeof search.q === "number" || typeof search.q === "boolean"
      ? String(search.q).trim().slice(0, 120) || undefined
      : undefined,
    category: typeof search.category === "string" ? search.category.trim().slice(0, 40) || undefined : undefined,
    page: typeof search.page === "number" && Number.isSafeInteger(search.page) && search.page > 1 ? search.page : undefined,
  }),
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

const activityRegistrationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/activities/registrations",
  component: page("./pages/ActivityRegistrationsPage", "ActivityRegistrationsPage"),
  validateSearch: (search: Record<string, unknown>): {activity_charge_id?: string; attempt_id?: string; payment?: string} => ({
    activity_charge_id: typeof search.activity_charge_id === "string" ? search.activity_charge_id : undefined,
    attempt_id: typeof search.attempt_id === "string" ? search.attempt_id : undefined,
    payment: typeof search.payment === "string" ? search.payment : undefined,
  }),
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
  component: page("./pages/MembershipAdmissionPage", "MembershipAdmissionPage"),
});

const membershipLegacyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/membership/legacy",
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
  validateSearch: (search: Record<string, unknown>): { q?: string; order_id?: string; section?: "orders" | "manual-invoice" | "summary" | "issues" | "suppliers" | "locations"; queue?: "refunds" | "business-unpaid" | "invoice" } => ({
    q: typeof search.q === "string" ? search.q.trim().slice(0, 100) : undefined,
    order_id: typeof search.order_id === "string" && search.order_id ? search.order_id : undefined,
    section: search.section === "orders" || search.section === "manual-invoice" || search.section === "summary" || search.section === "issues" || search.section === "suppliers" || search.section === "locations" ? search.section : undefined,
    queue: search.queue === "refunds" || search.queue === "business-unpaid" || search.queue === "invoice" ? search.queue : undefined,
  }),
  beforeLoad: ({ search }) => {
    if (search.section === "suppliers" || search.section === "locations") {
      throw redirect({ to: "/admin/catalog", search: { tab: search.section }, replace: true });
    }
  },
});

const adminSocialRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/social",
  component: page("./pages/AdminSocialPage", "AdminSocialPage"),
  validateSearch: (search: Record<string, unknown>): {
    tab?: "reviews" | "activities" | "proposals" | "wishes" | "meetings" | "members" | "roster" | "education";
    view?: "active" | "refunds" | "voting";
    q?: string;
  } => ({
    tab: search.tab === "activities" || search.tab === "proposals" || search.tab === "wishes" || search.tab === "meetings" || search.tab === "members" || search.tab === "roster" || search.tab === "education" ? search.tab : "reviews",
    view: search.tab === "activities" && (search.view === "active" || search.view === "refunds") ? search.view : search.tab === "proposals" && search.view === "voting" ? "voting" : undefined,
    q: search.tab === "members" && typeof search.q === "string" ? search.q.trim().slice(0, 100) : undefined,
  }),
  beforeLoad: ({ search }) => {
    if (search.tab === "wishes") throw redirect({ to: "/admin/groups", search: { tab: "wishes" }, replace: true });
    if (search.tab === "members" || search.tab === "roster") {
      throw redirect({ to: "/admin/users", search: { tab: search.tab, q: search.q }, replace: true });
    }
  },
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
  validateSearch: (search: Record<string, unknown>): { tab?: "proposals" | "campaigns" | "bundles" | "wishes"; view?: "active" | "decision" | "voting" } => ({
    tab: search.tab === "campaigns" || search.tab === "bundles" || search.tab === "wishes" ? search.tab : "proposals",
    view: search.tab === "campaigns" && (search.view === "active" || search.view === "decision") ? search.view : search.view === "voting" && (!search.tab || search.tab === "proposals") ? "voting" : undefined,
  }),
});

const adminMealsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/meals",
  validateSearch: (search: Record<string, unknown>): { tab?: string; q?: string; order_id?: string; date?: string; rebuild?: string; queue?: "today_business_cash_uncollected" } => ({
    tab: typeof search.tab === "string" && ["orders", "cancellations", "events", "schedules", "disaster", "meals"].includes(search.tab) ? search.tab : undefined,
    q: typeof search.q === "string" ? search.q.slice(0, 100) : undefined,
    queue: search.tab === "orders" && search.queue === "today_business_cash_uncollected" ? search.queue : undefined,
    order_id: search.tab === "cancellations" && typeof search.order_id === "string" && search.order_id.length > 0 && search.order_id.length <= 36
      ? search.order_id : undefined,
    date: search.tab === "events" && typeof search.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(search.date) ? search.date : undefined,
    rebuild: search.tab === "events" && typeof search.rebuild === "string" && /^[\w-]{1,36}$/.test(search.rebuild) ? search.rebuild : undefined,
  }),
  component: page("./pages/AdminMealsPage", "AdminMealsPage"),
});

const adminAccountingRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/accounting", component: page("./pages/AdminAccountingPage", "AdminAccountingPage") });

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

const adminRecordsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/records",
  validateSearch: (search: Record<string, unknown>): { tab?: "payments" | "points" | "spending"; member?: string } => ({
    tab: search.tab === "payments" || search.tab === "points" || search.tab === "spending" ? search.tab : undefined,
    member: typeof search.member === "string" ? search.member.trim().slice(0, 100) || undefined : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: "/admin/users", search: { tab: "records", ledger: search.tab, member: search.member }, replace: true });
  },
});

const adminPromotionsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/promotions", component: page("./pages/AdminPromotionsPage", "AdminPromotionsPage") });

const adminInvoicesRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/invoices", component: page("./pages/AdminInvoicesPage", "AdminInvoicesPage") });
const adminStatementsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/statements", component: page("./pages/AdminStatementsPage", "AdminStatementsPage") });

const adminSettingsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin/settings", component: page("./pages/AdminSettingsPage", "AdminSettingsPage") });

const adminUsersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/users",
  validateSearch: (search: Record<string, unknown>): {
    tab?: "members" | "roster" | "records";
    ledger?: "payments" | "points" | "spending";
    q?: string;
    membership?: string;
    member?: string;
  } => {
    const tab = search.tab === "members" || search.tab === "roster" || search.tab === "records" ? search.tab : undefined;
    return {
      tab,
      ledger: tab === "records" && (search.ledger === "payments" || search.ledger === "points" || search.ledger === "spending") ? search.ledger : undefined,
      q: tab === "members" && typeof search.q === "string" ? search.q.trim().slice(0, 100) || undefined : undefined,
      membership: tab && typeof search.membership === "string" ? search.membership.trim().slice(0, 100) || undefined : undefined,
      member: tab && typeof search.member === "string" ? search.member.trim().slice(0, 100) || undefined : undefined,
    };
  },
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

const memberLookupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/member-lookup",
  validateSearch: (search: Record<string, unknown>): { return_to?: string; remember_me?: boolean } => ({
    return_to: typeof search.return_to === "string" ? search.return_to : undefined,
    remember_me: search.remember_me === false || search.remember_me === "false" ? false : true,
  }),
  component: page("./pages/MemberLookupPage", "MemberLookupPage"),
});

const policyRoutes = [
  createRoute({ getParentRoute: () => rootRoute, path: '/privacy', component: page('./pages/PolicyPages', 'PrivacyPage') }),
  createRoute({ getParentRoute: () => rootRoute, path: '/terms', component: page('./pages/PolicyPages', 'TermsPage') }),
  createRoute({ getParentRoute: () => rootRoute, path: '/returns', component: page('./pages/PolicyPages', 'ReturnsPage') }),
];

const routeTree = rootRoute.addChildren([
  ...policyRoutes,
  loginRoute,
  lineLoginRoute,
  lineConsentRoute,
  memberLookupRoute,
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
  activityRegistrationsRoute,
  governanceRoute,
  meetingsRoute,
  proposalDetailRoute,
  wishesRoute,
  directoryRoute,
  membershipRoute,
  membershipLegacyRoute,
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
  adminAccountingRoute,
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
