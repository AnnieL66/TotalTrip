"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Header } from "@/components/Header";
import { Failure, Loading } from "@/components/Status";
import { api, money } from "@/lib/api";
import { useTrip } from "@/lib/useTrip";

const CATEGORIES = [
  { key: "flights", label: "Flights" },
  { key: "bags", label: "Bag fees" },
  { key: "rides", label: "Rides, transit and parking" },
  { key: "other", label: "Anything else you didn't expect" },
];

export default function AfterTrip() {
  const { id } = useParams<{ id: string }>();
  const { trip, error } = useTrip(id);
  const [values, setValues] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<string | null>(null); // null until the traveler chooses; "" means none of them
  const [note, setNote] = useState("");
  const [result, setResult] = useState<{ estimated_total: number | null; actual_total: number; difference: number | null } | null>(null);
  const [failed, setFailed] = useState(false);

  if (error) return <Failure message={error} />;
  if (!trip) return <Loading />;
  // Plan letters repeat between searches, so each choice names its search too.
  const latest = trip.plans;
  const followed = trip.followed_plan;
  const choiceKey = (searchId: string, planId: string) => `${searchId}:${planId}`;
  const choices = (latest?.plans ?? []).map((p) => ({
    key: choiceKey(latest!.search_id, p.id),
    label: `Plan ${p.id}: ${p.title} (${money(p.totals.likely)})`,
  }));
  if (followed && followed.search_id !== latest?.search_id) {
    choices.unshift({
      key: choiceKey(followed.search_id, followed.plan_id),
      label: `The plan you clicked through: ${followed.title} (${money(followed.likely)})`,
    });
  }
  const suggested = followed
    ? choiceKey(followed.search_id, followed.plan_id)
    : latest?.comparison
      ? choiceKey(latest.search_id, latest.comparison.recommended)
      : "";
  const chosen = picked ?? suggested;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const actual = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)]));
    const [search_id, plan_id] = chosen ? chosen.split(":") : [null, null];
    setFailed(false);
    try {
      setResult(await api.reportSpend(id, { plan_id, search_id, actual, note: note || null }));
    } catch {
      setFailed(true);
    }
  }

  return (
    <>
      <Header active="trips" />
      <main className="mx-auto max-w-xl px-4 py-10 sm:px-6">
        <Link href={`/trip/${id}/plans`} className="text-xs text-plum underline">
          ← Back to plans
        </Link>
        <h1 className="mt-2 font-serif text-3xl">What did you actually spend?</h1>
        <p className="mt-1 text-sm text-muted">Comparing real receipts with our estimates is how TotalTrip gets more accurate.</p>

        {result ? (
          <div className="mt-6 rounded-2xl bg-plum-50 p-5">
            <p className="font-serif text-xl">Thank you!</p>
            <p className="mt-1 text-sm">
              You spent {money(result.actual_total)}
              {result.estimated_total !== null && result.difference !== null && (
                <>
                  {" "}
                  against our estimate of {money(result.estimated_total)} (
                  {result.difference === 0 ? "spot on" : `${money(Math.abs(result.difference))} ${result.difference > 0 ? "more" : "less"}`}).
                </>
              )}
            </p>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-6 space-y-4 rounded-2xl border border-line bg-paper p-5">
            {choices.length > 0 && (
              <label className="flex flex-col gap-1 text-sm">
                Which plan did you follow?
                <select value={chosen} onChange={(e) => setPicked(e.target.value)} className="rounded-md border border-line bg-white px-2 py-1.5">
                  {choices.map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.label}
                    </option>
                  ))}
                  <option value="">None of them</option>
                </select>
              </label>
            )}
            {CATEGORIES.map((c) => (
              <label key={c.key} className="flex items-center justify-between gap-4 text-sm">
                {c.label}
                <span className="flex items-center gap-1">
                  $
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={values[c.key] ?? ""}
                    onChange={(e) => setValues({ ...values, [c.key]: e.target.value })}
                    className="w-28 rounded-md border border-line bg-white px-2 py-1.5 text-right"
                  />
                </span>
              </label>
            ))}
            <label className="flex flex-col gap-1 text-sm">
              Any surprises?
              <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="rounded-md border border-line bg-white px-2 py-1.5" />
            </label>
            <button
              type="submit"
              disabled={!Object.values(values).some((v) => v !== "")}
              className="rounded-full bg-plum px-5 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              Send
            </button>
            {failed && (
              <p className="text-sm text-warn" role="alert">
                Couldn&apos;t save your report. Check that at least one amount is above zero and try again.
              </p>
            )}
          </form>
        )}
      </main>
    </>
  );
}
