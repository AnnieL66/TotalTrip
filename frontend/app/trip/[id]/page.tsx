"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Badge } from "@/components/Badge";
import { BriefPanel } from "@/components/BriefPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { Header } from "@/components/Header";
import { Skyline } from "@/components/Skyline";
import { Failure, Loading } from "@/components/Status";
import { api } from "@/lib/api";
import { useTrip } from "@/lib/useTrip";

const NEEDED_LABELS: Record<string, string> = {
  origin: "where you're leaving from",
  destination: "where in Chicago you're going",
  depart_date: "departure date",
  return_date: "return date",
};

export default function TripBriefPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { trip, setTrip, error, send } = useTrip(id);
  const [searching, setSearching] = useState(false);
  const [notice, setNotice] = useState<string[]>([]);

  async function onSend(text: string) {
    const next = await send(text);
    setNotice([]);
    if (next.plans_searched) router.push(`/trip/${id}/plans`);
  }

  async function compare() {
    setSearching(true);
    setNotice([]);
    try {
      await api.searchPlans(id);
      router.push(`/trip/${id}/plans`);
    } catch (e) {
      setNotice([e instanceof Error ? e.message : "Couldn't compare plans."]);
      setSearching(false);
    }
  }

  if (error) return <Failure message={error} />;
  if (!trip) return <Loading />;

  const { brief } = trip;
  return (
    <>
      <Header active="trips" />
      <main className="mx-auto grid max-w-7xl gap-0 lg:h-[calc(100vh-3.5rem)] lg:grid-cols-[380px_1fr]">
        <aside className="flex h-[70vh] flex-col border-line bg-paper lg:h-auto lg:border-r">
          <ChatPanel
            title={<h1 className="font-serif text-2xl leading-tight">Where will your next story take you?</h1>}
            messages={trip.messages}
            onSend={onSend}
            suggestions={brief.ready ? ["Compare plans", "I'd rather spend less", "I want the easiest trip"] : []}
          />
        </aside>

        <section className="space-y-5 overflow-y-auto p-4 sm:p-6">
          <div className="overflow-hidden rounded-2xl border border-line bg-paper">
            <Skyline className="h-36 w-full object-cover sm:h-44" />
            <div className="p-5">
              <h2 className="font-serif text-2xl">A better trip starts with a conversation</h2>
              <p className="mt-1 text-sm text-muted">
                As we talk, I fill in your trip brief. Check it and fix anything I got wrong; every plan is built from it.
              </p>
            </div>
          </div>

          <div className="rounded-2xl border border-line bg-paper p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-serif text-xl">Your trip is taking shape</h2>
              <div className="flex gap-1.5">
                <Badge kind="user_said" />
                <Badge kind="assumed" />
              </div>
            </div>
            <div className="mt-4">
              <BriefPanel
                tripId={id}
                brief={brief}
                onChange={(b, errors) => {
                  setTrip({ ...trip, brief: b });
                  setNotice(errors);
                }}
              />
            </div>
            {notice.length > 0 && (
              <ul className="mt-3 space-y-1 rounded-lg bg-warn-100 px-3 py-2 text-sm text-warn" role="alert">
                {notice.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
            <div className="mt-5 flex flex-wrap items-end justify-between gap-4 border-t border-line pt-4">
              <div className="text-sm">
                {brief.ready ? (
                  <>
                    <p className="font-medium">Ready to compare</p>
                    <p className="text-muted">Click any detail to change it. Assumptions are in orange.</p>
                  </>
                ) : (
                  <>
                    <p className="font-medium">Still needed</p>
                    <p className="text-muted">{brief.missing.map((m) => NEEDED_LABELS[m] ?? m).join(" · ")}</p>
                  </>
                )}
              </div>
              <div className="text-right">
                <button
                  type="button"
                  onClick={compare}
                  disabled={!brief.ready || searching}
                  className="rounded-full bg-plum px-5 py-2.5 text-sm font-medium text-white disabled:bg-plum-100 disabled:text-plum"
                >
                  {searching ? "Comparing…" : "Compare complete plans"}
                </button>
                {trip.plans && (
                  <p className="mt-1.5 text-xs">
                    <Link href={`/trip/${id}/plans`} className="text-plum underline">
                      See your last plans
                    </Link>
                  </p>
                )}
              </div>
            </div>
          </div>
        </section>
      </main>
    </>
  );
}
