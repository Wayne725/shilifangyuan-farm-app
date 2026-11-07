import type { AuthResponse, User } from "./types";
import { fetchWithTimeout, RequestTimeoutError } from "./http";
import { localStore } from "./browser-storage";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
let accessToken: string | null = null;
let currentSession: AuthResponse | null = null;
let credentialRevision = 0;
let identityRevision = 0;
let refreshPromise: Promise<AuthResponse | null> | null = null;
const sessionListeners = new Set<(user: User | null, identityChanged: boolean) => void>();

if (typeof window !== "undefined") {
  localStore.removeItem("slf_access_token");
  localStore.removeItem("slf_refresh_token");
  localStore.removeItem("slf_user");
}

export class ApiError extends Error {
  status: number;
  code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function isCurrentSessionUser(userId: string): boolean {
  return currentSession?.user.id === userId;
}

export function captureSessionIdentityGuard(): () => boolean {
  const startedAt = identityRevision;
  const userId = currentSession?.user.id;
  return () => Boolean(userId) && identityRevision === startedAt && currentSession?.user.id === userId;
}

function identityKey(user?: User | null): string {
  return user ? JSON.stringify([user.id, user.user_role, user.membership_type, user.membership_status]) : "anonymous";
}

function publishSession(session: AuthResponse | null): void {
  const identityChanged = identityKey(currentSession?.user) !== identityKey(session?.user);
  currentSession = session;
  if (identityChanged) identityRevision += 1;
  for (const listener of sessionListeners) listener(session?.user ?? null, identityChanged);
}

export function subscribeSession(listener: (user: User | null, identityChanged: boolean) => void): () => void {
  sessionListeners.add(listener);
  return () => { sessionListeners.delete(listener); };
}

export function updateSessionUser(user: User): void {
  if (!currentSession || currentSession.user.id !== user.id) throw sessionChangedError();
  publishSession({ ...currentSession, user });
}

export function saveSession(session: AuthResponse): void {
  accessToken = session.access_token;
  credentialRevision += 1;
  refreshPromise = null;
  publishSession(session);
}

export function clearSession(): void {
  accessToken = null;
  credentialRevision += 1;
  refreshPromise = null;
  publishSession(null);
}

function sessionChangedError(): ApiError {
  return new ApiError(409, "登入身分已變更，請確認目前帳號後重新操作。");
}

function requireSameIdentity(revision: number): void {
  if (revision !== identityRevision) throw sessionChangedError();
}

function apiUrl(path: string): string {
  return apiBaseUrl && path.startsWith("/") ? `${apiBaseUrl}${path}` : path;
}

async function requestApi(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  try {
    return await fetchWithTimeout(apiUrl(path), {
      ...init,
      credentials: "include",
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    const message = error instanceof RequestTimeoutError
      ? "後端服務回應逾時，可能正在重新啟動，請稍後重試。"
      : "目前無法連上後端服務，請稍後重試。";
    throw new ApiError(503, message);
  }
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  let message = "連線失敗，請稍後再試";
  try {
    const body = (await response.json()) as {
      detail?: string | Array<{ msg?: string }>;
    };
    if (typeof body.detail === "string") message = body.detail;
    if (Array.isArray(body.detail)) {
      message = body.detail.map((item) => item.msg).filter(Boolean).join("、");
    }
  } catch {
    // Response is not JSON.
  }
  return new ApiError(response.status, message, response.headers.get("X-Error-Code"));
}

function nativeLockUnavailable(error: unknown): boolean {
  return typeof DOMException !== "undefined" && error instanceof DOMException
    && ["SecurityError", "NotSupportedError"].includes(error.name);
}

async function requestSessionRefresh(stillCurrent: () => boolean): Promise<Response | null> {
  const send = () => stillCurrent() ? requestApi("/v1/auth/refresh", { method: "POST" }) : Promise.resolve(null);
  let manager: LockManager | undefined;
  try {
    manager = typeof navigator === "undefined" ? undefined : navigator.locks;
  } catch (error) {
    if (nativeLockUnavailable(error)) return send();
    throw error;
  }
  if (!manager || typeof manager.request !== "function") return send();
  const controller = new AbortController();
  let entered = false;
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const waitingError = () => new ApiError(503, "登入狀態暫時無法更新，請重試連線。");
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => { timedOut = true; controller.abort(); reject(waitingError()); }, 15_000);
  });
  try {
    const request = manager.request("slf-auth-refresh-v1", { mode: "exclusive", signal: controller.signal }, () => {
      entered = true;
      if (timer !== undefined) clearTimeout(timer);
      if (timedOut) throw waitingError();
      return send();
    });
    return await Promise.race([request, timeout]);
  } catch (error) {
    if (!entered && timedOut) throw waitingError();
    if (!entered && nativeLockUnavailable(error)) return send();
    throw error;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

export async function restoreSession(): Promise<AuthResponse | null> {
  if (refreshPromise) return refreshPromise;
  const startedAt = credentialRevision;
  const startedIdentity = identityRevision;
  const stillCurrent = () => startedAt === credentialRevision && startedIdentity === identityRevision;
  const restoring = (async () => {
    try {
      const response = await requestSessionRefresh(stillCurrent);
      if (!response || !stillCurrent()) return currentSession;
      if (response.status === 401) {
        clearSession();
        return null;
      }
      if (!response.ok) throw await errorFromResponse(response);
      const session = (await response.json()) as AuthResponse;
      if (!stillCurrent()) return currentSession;
      saveSession(session);
      return session;
    } catch (error) {
      if (!stillCurrent()) return currentSession;
      throw error;
    } finally {
      if (startedAt === credentialRevision) refreshPromise = null;
    }
  })();
  refreshPromise = restoring;
  return restoring;
}

async function authenticatedResponse(
  path: string,
  init: RequestInit = {},
): Promise<{ response: Response; identity: number }> {
  const identity = identityRevision;
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let requestedCredentials = credentialRevision;
  const request = () => {
    requestedCredentials = credentialRevision;
    return requestApi(path, { ...init, headers });
  };
  let response = await request();
  requireSameIdentity(identity);
  const cannotRefresh = new Set([
    "/v1/auth/login",
    "/v1/auth/refresh",
    "/v1/auth/register",
    "/v1/auth/register-existing-member",
    "/v1/auth/member-lookup",
    "/v1/auth/verify-email",
    "/v1/auth/resend-verification",
    "/v1/auth/forgot-password",
    "/v1/auth/reset-password",
    "/v1/auth/line/start",
    "/v1/auth/line/callback",
    "/v1/auth/line/register",
    "/v1/auth/line/pending",
  ]);
  if (response.status === 401 && !cannotRefresh.has(path)) {
    if (token === getAccessToken()) await restoreSession();
    requireSameIdentity(identity);
    const refreshedToken = getAccessToken();
    if (refreshedToken) {
      headers.set("Authorization", `Bearer ${refreshedToken}`);
      response = await request();
      requireSameIdentity(identity);
    }
  }
  if (!response.ok) {
    if (response.status === 401 && !cannotRefresh.has(path) && requestedCredentials === credentialRevision) clearSession();
    throw await errorFromResponse(response);
  }
  return { response, identity };
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const { response, identity } = await authenticatedResponse(path, init);
  if (response.status === 204) return undefined as T;
  const result = await response.json() as T;
  requireSameIdentity(identity);
  return result;
}

export async function fetchApiBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const { response, identity } = await authenticatedResponse(path, { signal });
  const blob = await response.blob();
  requireSameIdentity(identity);
  return blob;
}

export async function downloadApiFile(path: string, filename: string): Promise<void> {
  const objectUrl = URL.createObjectURL(await fetchApiBlob(path));
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}

export function resolveAsset(imageUrl?: string | null): string {
  if (!imageUrl) return "/products/generic-product.jpg";
  if (imageUrl.startsWith("/v1/meal-photos/") || imageUrl.startsWith("/v1/product-photos/")) return apiUrl(imageUrl);
  return imageUrl.replace(/^\/assets/, "");
}

export function replaceBrokenAsset(event: {
  currentTarget: HTMLImageElement;
}): void {
  const fallback = resolveAsset();
  if (event.currentTarget.getAttribute("src") !== fallback) {
    event.currentTarget.src = fallback;
  }
}

export function formatMoney(value: number): string {
  return new Intl.NumberFormat("zh-TW", {
    style: "currency",
    currency: "TWD",
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatDate(value?: string | null): string {
  if (!value) return "日期待公告";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(date);
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "時間待公告";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", {
    month: "numeric",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
