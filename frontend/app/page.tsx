"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Header } from "@/components/Header";
import { api, captureTester, money, rememberTrip } from "@/lib/api";

// One-click trips; the backend defines each one in app/data/demo_trips.json.
const DEMOS = [
  { key: "newark_trap", label: "A weekend with a checked bag" },
  { key: "newark_fits", label: "A work trip from Hoboken" },
  { key: "tight_budget", label: "Visiting friends on a budget" },
];

const INSPIRATION = [
  { title: "A weekend by the lake", body: "Millennium Park, the Riverwalk and deep dish.", text: "A weekend near Millennium Park, leaving from " },
  { title: "Wrigley on a weeknight", body: "Catch a game and stay a couple of nights.", text: "Going to a game at Wrigley Field, leaving from " },
  { title: "Visiting friends", body: "Wicker Park, Logan Square and the Blue Line.", text: "Visiting friends in Wicker Park, leaving from " },
];

// Illustrative example for the explainer card: the "newark_trap" sample trip, on demo fares. A backend test keeps these numbers in step.
const EXAMPLE = [
  { airport: "Newark", flights: 252, rides: 259 },
  { airport: "LaGuardia", flights: 301, rides: 174 },
];

export default function Discover() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(captureTester, []);

  async function startDemo(demo: (typeof DEMOS)[number]) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const trip = await api.createDemoTrip(demo.key);
      rememberTrip(trip.id, demo.label);
      router.push(`/trip/${trip.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start that trip. Please try again.");
      setBusy(false);
    }
  }

  function prefill(value: string) {
    setText(value);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(value.length, value.length);
      }
    });
  }

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const trip = await api.createTrip(text.trim());
      rememberTrip(trip.id, text.trim().slice(0, 80));
      router.push(`/trip/${trip.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start your trip. Please try again.");
      setBusy(false);
    }
  }

  const max = Math.max(...EXAMPLE.map((r) => r.flights + r.rides));

  return (
    <>
      <Header active="discover" />
      <main className="mx-auto max-w-4xl px-4 pb-20 pt-12 sm:px-6 sm:pt-16">
        <div className="text-center">
          <span className="inline-block rounded-full bg-plum-100 px-3 py-1 text-xs font-medium text-plum">
            Trip intelligence for the whole trip
          </span>
          <h1 className="mt-5 font-serif text-4xl font-medium tracking-tight sm:text-6xl">Where do you want to go?</h1>
          <p className="mx-auto mt-4 max-w-xl text-muted">
            Tell us about your trip. We&apos;ll compare flights and every ride to and from the airport, and show you what the
            whole trip costs before you book.
          </p>
        </div>

        <form onSubmit={start} className="mx-auto mt-8 max-w-2xl">
          <div className="flex items-center gap-2 rounded-full border border-line bg-paper py-1.5 pl-5 pr-1.5 shadow-sm focus-within:border-plum-600">
            <label htmlFor="trip-input" className="sr-only">
              Describe your trip
            </label>
            <input
              id="trip-input"
              ref={inputRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="A weekend near Millennium Park from the Upper West Side, Nov 12–16, under $500…"
              className="min-w-0 flex-1 bg-transparent py-2 outline-none placeholder:text-muted"
              autoComplete="off"
            />
            <button
              type="submit"
              disabled={!text.trim() || busy}
              aria-label="Start planning"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-plum text-white disabled:opacity-40"
            >
              {busy ? "…" : "→"}
            </button>
          </div>
          {error && <p className="mt-2 text-center text-sm text-warn">{error}</p>}
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {DEMOS.map((d) => (
              <button
                key={d.key}
                type="button"
                onClick={() => startDemo(d)}
                disabled={busy}
                className="rounded-full border border-line bg-paper px-3.5 py-1.5 text-sm hover:border-plum-600 disabled:opacity-50"
              >
                {d.label}
              </button>
            ))}
          </div>
          <p className="mt-3 text-center text-xs text-muted">Now planning solo trips from New York City to Chicago.</p>
        </form>

        <section className="mt-14 grid gap-6 rounded-2xl border border-line bg-paper p-6 sm:grid-cols-[1fr_1.2fr] sm:p-8">
          <div>
            <h2 className="font-serif text-2xl leading-snug">The cheapest flight isn&apos;t always the cheapest trip.</h2>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              From the Upper West Side with a checked bag, Newark flights that are {money(EXAMPLE[1].flights - EXAMPLE[0].flights)} cheaper
              end up costing more once you count the rides to and from the airport. TotalTrip counts them for you.
            </p>
          </div>
          <div className="flex flex-col justify-center gap-3" role="img" aria-label="Illustrative totals on demo fares: Newark costs more door to door than LaGuardia">
            {EXAMPLE.map((r) => {
              const total = r.flights + r.rides;
              return (
                <div key={r.airport} className="grid grid-cols-[80px_1fr_48px] items-center gap-3 text-sm">
                  <span className="font-medium">{r.airport}</span>
                  <div className="flex h-4 overflow-hidden rounded-full bg-sand" style={{ width: `${(total / max) * 100}%` }}>
                    <div className="bg-plum-600" style={{ width: `${(r.flights / total) * 100}%` }} />
                    <div className="bg-ride" style={{ width: `${(r.rides / total) * 100}%` }} />
                  </div>
                  <span className="text-right font-semibold tabular-nums">{money(total)}</span>
                </div>
              );
            })}
            <div className="mt-1 flex gap-4 text-xs text-muted">
              <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-sm bg-plum-600" /> Flights + bags</span>
              <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-sm bg-ride" /> Rides to and from the airport</span>
              <span className="ml-auto">Illustrative demo fares</span>
            </div>
          </div>
        </section>

        <section className="mt-12">
          <h2 className="font-serif text-2xl">A little inspiration for your next trip</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            {INSPIRATION.map((c, i) => (
              <button
                key={c.title}
                type="button"
                onClick={() => prefill(c.text)}
                className={`rounded-2xl p-5 text-left transition hover:-translate-y-0.5 ${["bg-sand", "bg-ride-100", "bg-plum-50"][i]}`}
              >
                <p className="font-serif text-lg">{c.title}</p>
                <p className="mt-1 text-sm text-muted">{c.body}</p>
                <p className="mt-4 text-xs font-medium text-plum">Plan this trip →</p>
              </button>
            ))}
          </div>
        </section>
      </main>
    </>
  );
}
