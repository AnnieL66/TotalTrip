import type { PlanSet, TripState } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error body */
    }
    const message =
      typeof detail === "string" ? detail : (detail as { message?: string })?.message ?? `Request failed (${res.status})`;
    throw new ApiError(res.status, message, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  createTrip: (message?: string) => request<TripState>("/trips", { method: "POST", body: JSON.stringify({ message }) }),
  getTrip: (id: string) => request<TripState>(`/trips/${id}`),
  sendMessage: (id: string, text: string) =>
    request<TripState>(`/trips/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  editBrief: (id: string, updates: Record<string, unknown>) =>
    request<TripState>(`/trips/${id}/brief`, { method: "PATCH", body: JSON.stringify(updates) }),
  searchPlans: (id: string, recheck = false) =>
    request<PlanSet>(`/trips/${id}/plans${recheck ? "?recheck=true" : ""}`, { method: "POST" }),
  places: () => request<{ NYC: string[]; CHI: string[] }>("/places"),
  meta: () => request<{ assistant_mode: string; flight_data: string; routing: string }>("/meta"),
  event: (name: string, trip_id?: string, props: Record<string, unknown> = {}) =>
    request("/events", { method: "POST", body: JSON.stringify({ name, trip_id, props }) }).catch(() => undefined),
  reportPrice: (id: string, body: Record<string, unknown>) =>
    request(`/trips/${id}/price-reports`, { method: "POST", body: JSON.stringify(body) }),
  reportSpend: (id: string, body: Record<string, unknown>) =>
    request<{ estimated_total: number | null; actual_total: number; difference: number | null }>(`/trips/${id}/spend`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export function outboundUrl(url: string, tripId: string, planId: string, searchId: string) {
  const q = new URLSearchParams({ url, trip: tripId, plan: planId, search: searchId });
  return `/api/out?${q.toString()}`;
}

export const money = (n: number) => `$${Math.round(n).toLocaleString("en-US")}`;

/** Remember trips on this device so "My trips" works without accounts. */
export function rememberTrip(id: string, label: string) {
  try {
    const list: { id: string; label: string; at: string }[] = JSON.parse(localStorage.getItem("tt-trips") ?? "[]");
    const next = [{ id, label, at: new Date().toISOString() }, ...list.filter((t) => t.id !== id)].slice(0, 20);
    localStorage.setItem("tt-trips", JSON.stringify(next));
  } catch {
    /* storage unavailable */
  }
}

export function recentTrips(): { id: string; label: string; at: string }[] {
  try {
    return JSON.parse(localStorage.getItem("tt-trips") ?? "[]");
  } catch {
    return [];
  }
}
