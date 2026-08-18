import type { AuthResponse } from "./types";

const accessTokenKey = "slf_access_token";
const refreshTokenKey = "slf_refresh_token";
const userKey = "slf_user";
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getAccessToken(): string | null {
  return localStorage.getItem(accessTokenKey);
}

export function saveSession(session: AuthResponse): void {
  localStorage.setItem(accessTokenKey, session.access_token);
  localStorage.setItem(refreshTokenKey, session.refresh_token);
  localStorage.setItem(userKey, JSON.stringify(session.user));
}

export function clearSession(): void {
  localStorage.removeItem(accessTokenKey);
  localStorage.removeItem(refreshTokenKey);
  localStorage.removeItem(userKey);
}

export function readStoredUser(): AuthResponse["user"] | null {
  const value = localStorage.getItem(userKey);
  if (!value) return null;
  try {
    return JSON.parse(value) as AuthResponse["user"];
  } catch {
    clearSession();
    return null;
  }
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

  const url = apiBaseUrl && path.startsWith("/") ? `${apiBaseUrl}${path}` : path;
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
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
    if (response.status === 401) clearSession();
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function downloadApiFile(path: string, filename: string): Promise<void> {
  const headers = new Headers();
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const url = apiBaseUrl && path.startsWith("/") ? `${apiBaseUrl}${path}` : path;
  const response = await fetch(url, { headers });
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

export function mealEventStatusLabel(status: string): string {
  return {
    draft: "草稿",
    published: "開放預訂",
    ordering_closed: "預訂截止",
    pickup_open: "開放取餐",
    cancelled: "已取消",
    completed: "已完成",
  }[status] || status;
}
