import type { AuthResponse } from "./types";
import { fetchWithTimeout, RequestTimeoutError } from "./http";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
let accessToken: string | null = null;
let refreshPromise: Promise<AuthResponse | null> | null = null;

if (typeof window !== "undefined") {
  localStorage.removeItem("slf_access_token");
  localStorage.removeItem("slf_refresh_token");
  localStorage.removeItem("slf_user");
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function saveSession(session: AuthResponse): void {
  accessToken = session.access_token;
}

export function clearSession(): void {
  accessToken = null;
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
  return new ApiError(response.status, message);
}

export async function restoreSession(): Promise<AuthResponse | null> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const response = await requestApi("/v1/auth/refresh", {
        method: "POST",
      });
      if (!response.ok) {
        clearSession();
        return null;
      }
      const session = (await response.json()) as AuthResponse;
      saveSession(session);
      return session;
    } catch {
      clearSession();
      return null;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const request = () => requestApi(path, {
    ...init,
    headers,
  });
  let response = await request();
  const cannotRefresh = new Set([
    "/v1/auth/login",
    "/v1/auth/refresh",
    "/v1/auth/register",
    "/v1/auth/register-existing-member",
    "/v1/auth/verify-email",
    "/v1/auth/resend-verification",
    "/v1/auth/forgot-password",
    "/v1/auth/reset-password",
  ]);
  if (response.status === 401 && !cannotRefresh.has(path)) {
    const restored = await restoreSession();
    if (restored) {
      headers.set("Authorization", `Bearer ${restored.access_token}`);
      response = await request();
    }
  }
  if (!response.ok) {
    if (response.status === 401) clearSession();
    throw await errorFromResponse(response);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function downloadApiFile(path: string, filename: string): Promise<void> {
  const request = () => {
    const headers = new Headers();
    const token = getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return requestApi(path, { headers });
  };
  let response = await request();
  if (response.status === 401 && await restoreSession()) response = await request();
  if (!response.ok) throw new ApiError(response.status, "報表下載失敗");
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}

export function resolveAsset(imageUrl?: string | null): string {
  if (!imageUrl) return "/products/generic-product.jpg";
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
