"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { BriefPanel } from "@/components/BriefPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { ConceptQuestion } from "@/components/ConceptQuestion";
import { Header } from "@/components/Header";
import { PlanCard } from "@/components/PlanCard";
import { Failure, Loading } from "@/components/Status";
import { api } from "@/lib/api";
import type { PlanSet } from "@/lib/types";
import { useTrip } from "@/lib/useTrip";

const WORDS = ["No", "One", "Two", "Three"];

export default function PlansPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { trip, setTrip, error, send } = useTrip(id);
  const [busy, setBusy] = useState<"search" | "recheck" | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [choice, setChoice] = useState<{ searchId: string; planId: string | null } | null>(null);
  const [choiceError, setChoiceError] = useState<string | null>(null);
  const viewed = useRef<string | null>(null);

  const plans = trip?.plans ?? null;

  useEffect(() => {
    if (trip && !trip.plans) router.replace(`/trip/${id}`);
  }, [trip, id, router]);

  useEffect(() => {
    if (plans && viewed.current !== plans.search_id) {
      viewed.current = plans.search_id;
      api.event("plans_viewed", id, { search_id: plans.search_id, plans: plans.plans.length });
    }
  }, [plans, id]);

  async function run(recheck: boolean) {
    setBusy(recheck ? "recheck" : "search");
    setSearchError(null);
    try {
      const next: PlanSet = await api.searchPlans(id, recheck);
      setTrip((t) => (t ? { ...t, plans: next } : t));
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : "Couldn't refresh plans.");
    } finally {
      setBusy(null);
    }
  }

  if (error) return <Failure message={error} />;
  if (!trip || !plans) return <Loading />;

  const demo = plans.data_mode === "demo";
  // Plan letters only mean something within one search, so a new search clears the choice.
  const chosen = choice?.searchId === plans.search_id ? choice : null;
  async function choose(planId: string | null) {
    if (!plans || (chosen && chosen.planId === planId)) return;
    const next = { searchId: plans.search_id, planId };
    setChoice(next);
    setChoiceError(null);
    try {
      await api.choosePlan(id, plans.search_id, planId);
    } catch {
      // Don't show a choice as made if it wasn't saved. A newer click stays as it is.
      setChoice((current) => (current === next ? chosen : current));
      setChoiceError("Couldn't save your choice. Please try again.");
    }
  }

  const field = (n: string) => trip.brief.fields.find((f) => f.name === n);
  const checked = new Date(plans.checked_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const assumed = trip.brief.fields.filter((f) => f.provenance === "assumed" && f.name !== "travelers");
  const dateRange = [field("depart_date")?.display, field("return_date")?.display]
    .map((d) => d?.replace(/, \d{4}$/, ""))
    .join(" – ");

  return (
    <>
      <Header active="trips" />
      <main className="mx-auto grid max-w-7xl lg:h-[calc(100vh-3.5rem)] lg:grid-cols-[300px_1fr]">
        <aside className="flex flex-col border-line bg-paper lg:min-h-0 lg:border-r">
          <div className="border-b border-line p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-serif text-lg">Your trip brief</h2>
              <Link href={`/trip/${id}`} className="rounded-full border border-line px-3 py-0.5 text-xs hover:border-plum-600">
                Edit
              </Link>
            </div>
            <div className="mt-2">
              <BriefPanel tripId={id} brief={trip.brief} onChange={(b) => setTrip({ ...trip, brief: b })} compact />
            </div>
            {assumed.length > 0 && (
              <p className="mt-3 rounded-lg bg-warn-100 px-3 py-2 text-xs text-warn">
                Assumed: {assumed.map((f) => f.display?.toLowerCase()).join(", ")}.{" "}
                <Link href={`/trip/${id}`} className="font-semibold underline">
                  Confirm
                </Link>
              </p>
            )}
          </div>
          <div className="flex h-[55vh] min-h-0 flex-col lg:h-auto lg:flex-1">
            <ChatPanel
              compact
              messages={trip.messages.slice(-12)}
              onSend={async (t) => {
                await send(t);
              }}
              placeholder="Ask about these plans…"
              suggestions={["Why is the recommended plan better?", "Tell me about plan A"]}
            />
          </div>
        </aside>

        <section className="overflow-y-auto p-4 sm:p-6">
          <p className="text-xs text-muted">
            New York → Chicago · {dateRange} · 1 traveler
          </p>
          <h1 className="mt-1 font-serif text-3xl">
            {WORDS[plans.plans.length] ?? plans.plans.length} complete {plans.plans.length === 1 ? "plan" : "plans"}, door to door
          </h1>
          <p className="mt-1 text-sm text-muted">
            Every total runs from your front door in New York to your front door back: flights, bags and getting around Chicago.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
            <span className="text-muted">
              Prices checked {checked} · compared {plans.candidates_considered} combinations
            </span>
            <button
              type="button"
              onClick={() => run(true)}
              disabled={!!busy}
              className="rounded-full border border-line px-3 py-1 font-medium hover:border-plum-600 disabled:opacity-50"
            >
              {busy === "recheck" ? "Rechecking…" : "Recheck"}
            </button>
            <span className="text-muted sm:ml-auto">
              {!demo && "Flights, bags and transit fares are quotes. "}
              <span className="font-medium text-ink">~</span> marks an estimate, with its likely range.
            </span>
          </div>

          {!plans.is_current && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ride bg-ride-100 px-4 py-3 text-sm" role="status">
              <span>Your brief changed since these plans were priced.</span>
              <button type="button" onClick={() => run(false)} disabled={!!busy} className="rounded-full bg-plum px-4 py-1.5 text-white disabled:opacity-50">
                {busy === "search" ? "Updating…" : "Update plans"}
              </button>
            </div>
          )}
          {searchError && <p className="mt-3 text-sm text-warn" role="alert">{searchError}</p>}
          {plans.notes.map((n) => (
            <p key={n} className="mt-3 rounded-lg bg-warn-100 px-3 py-2 text-sm text-warn">
              {n}
            </p>
          ))}

          {plans.plans.length === 0 ? (
            <p className="mt-8 text-muted">No complete plans found for these dates. Try different dates in your brief.</p>
          ) : (
            <div className="mt-5 grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr))]">
              {plans.plans.map((p) => (
                <PlanCard
                  key={`${plans.search_id}-${p.id}`}
                  plan={p}
                  tripId={id}
                  searchId={plans.search_id}
                  demo={demo}
                  chosen={chosen?.planId === p.id}
                  onChoose={() => choose(p.id)}
                />
              ))}
            </div>
          )}

          {choiceError && <p className="mt-3 text-sm text-warn" role="alert">{choiceError}</p>}

          {demo && plans.plans.length > 0 && (
            <div className="mt-4 space-y-3">
              <button
                type="button"
                onClick={() => choose(null)}
                aria-pressed={chosen?.planId === null}
                className={`text-sm underline ${chosen?.planId === null ? "font-medium text-plum" : "text-muted hover:text-ink"}`}
              >
                None of these would work for me
              </button>
              {chosen?.planId === null && (
                <div className="max-w-xl">
                  <ConceptQuestion tripId={id} searchId={plans.search_id} planId={null} />
                </div>
              )}
            </div>
          )}

          {plans.comparison && (
            <div className="mt-5 rounded-2xl bg-plum-50 px-5 py-4">
              <p className="font-serif text-lg">{plans.comparison.headline}</p>
              {plans.comparison.detail && <p className="mt-1 text-sm text-muted">{plans.comparison.detail}</p>}
            </div>
          )}

          <footer className="mt-8 space-y-1 border-t border-line pt-4 text-xs text-muted">
            {demo ? (
              <p>Flight prices are illustrative demo fares, not live quotes. Nothing here gets booked or charged.</p>
            ) : (
              <>
                <p>A plan isn&apos;t a reservation. Prices can change until you book each part on the provider&apos;s own site.</p>
                <p>
                  Back from this trip?{" "}
                  <Link href={`/trip/${id}/after`} className="text-plum underline">
                    Tell us what you actually spent
                  </Link>{" "}
                  so our estimates get better.
                </p>
              </>
            )}
          </footer>
        </section>
      </main>
    </>
  );
}
