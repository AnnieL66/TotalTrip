import type { PlanSet, TripState } from "./types";

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
      typeof detail === "string" ? detail : (detail as { message?: string })?.message ?? "Something went wrong. Please check your details and try again.";
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

const TESTER_KEY = "tt-tester";
let tester: string | null = null;

/** Keep the tester code from a shared link (/?t=code), so results can be counted per person. An empty ?t= clears it. */
export function captureTester() {
  const raw = new URLSearchParams(window.location.search).get("t");
  if (raw === null) return;
  tester = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/.test(raw) ? raw : null;
  try {
    if (tester) localStorage.setItem(TESTER_KEY, tester);
    else localStorage.removeItem(TESTER_KEY);
  } catch {
    /* storage unavailable; the code still lasts for this visit */
  }
}

function testerCode(): string | undefined {
  if (tester) return tester;
  try {
    return localStorage.getItem(TESTER_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

export const api = {
  createTrip: (message?: string) => request<TripState>("/trips", { method: "POST", body: JSON.stringify({ message, tester: testerCode() }) }),
  createDemoTrip: (key: string) =>
    request<TripState>(`/trips/demo/${key}`, { method: "POST", body: JSON.stringify({ tester: testerCode() }) }),
  getTrip: (id: string) => request<TripState>(`/trips/${id}`),
  sendMessage: (id: string, text: string) =>
    request<TripState>(`/trips/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  editBrief: (id: string, updates: Record<string, unknown>) =>
    request<TripState>(`/trips/${id}/brief`, { method: "PATCH", body: JSON.stringify(updates) }),
  searchPlans: (id: string, recheck = false) =>
    request<PlanSet>(`/trips/${id}/plans${recheck ? "?recheck=true" : ""}`, { method: "POST" }),
  places: () => request<{ NYC: string[]; CHI: string[] }>("/places"),
  event: (name: string, trip_id?: string, props: Record<string, unknown> = {}) =>
    request("/events", { method: "POST", body: JSON.stringify({ name, trip_id, props }) }).catch(() => undefined),
  choosePlan: (id: string, search_id: string, plan_id: string | null) =>
    request(`/trips/${id}/plan-choice`, { method: "POST", body: JSON.stringify({ search_id, plan_id }) }),
  answerConcept: (id: string, body: { search_id: string; plan_id: string | null; would_change: string; note: string | null }) =>
    request(`/trips/${id}/concept-answer`, { method: "POST", body: JSON.stringify(body) }),
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
