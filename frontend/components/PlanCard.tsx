"use client";

import { useState } from "react";
import { api, money, outboundUrl } from "@/lib/api";
import type { LineItem, Plan } from "@/lib/types";
import { Badge } from "./Badge";

const MODE_ICON: Record<string, string> = { flight: "✈", bags: "🧳", rideshare: "🚗", transit: "🚆", drive: "🚙", parking: "🅿" };

export function PlanCard({ plan, tripId, searchId }: { plan: Plan; tripId: string; searchId: string }) {
  const [open, setOpen] = useState(false);
  const [reporting, setReporting] = useState(false);
  const bookings = mergeFlights(plan);
  const around = plan.items.filter((i) => i.group === "getting_around");
  const [route, ...rest] = plan.title.replace(/^Fly /, "").split(", ");
  const { outbound: out, return: back } = plan.flights;
  const airlines = out.airline === back.airline ? out.airline : `${out.airline} / ${back.airline}`;
  const how = [rest.join(", "), airlines, `leave home ${plan.door_to_door.leave_home}`].filter(Boolean).join(" · ");

  function toggle() {
    if (!open) api.event("plan_explored", tripId, { plan: plan.id, search_id: searchId });
    setOpen(!open);
  }

  return (
    <article
      className={`flex flex-col rounded-2xl border bg-paper p-5 ${plan.recommended ? "border-plum-600 ring-1 ring-plum-600" : "border-line"}`}
      aria-label={`Plan ${plan.letter}`}
    >
      <div className="flex flex-wrap gap-1.5">
        <Badge kind={plan.recommended ? "plum" : "neutral"}>Plan {plan.letter}</Badge>
        {plan.tags.map((t) => (
          <span key={t} className="text-[11px] font-medium text-muted">
            {t}
          </span>
        ))}
      </div>

      <div className="mt-2 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-serif text-xl leading-tight">{route}</h3>
          <p className="mt-0.5 text-xs text-muted">{how}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-serif text-3xl font-medium leading-none tabular-nums">{money(plan.totals.likely)}</p>
          <p className="mt-1 text-[11px] text-muted">
            {plan.totals.low === plan.totals.high ? "door to door" : `likely ${money(plan.totals.low)}–${money(plan.totals.high)}`}
          </p>
        </div>
      </div>

      {(plan.budget || plan.warnings.length > 0) && (
        <ul className="mt-3 space-y-1 text-xs">
          {plan.budget && (
            <li className={plan.budget.status === "under" ? "text-good" : "text-warn"}>
              {plan.budget.status === "under" ? "✓" : "⚠"} {plan.budget.message}
            </li>
          )}
          {plan.warnings.map((w) => (
            <li key={w} className="text-warn">
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}

      <Section title="Bookings" total={plan.totals.bookings} items={bookings} />
      <Section title="Getting around" total={plan.totals.getting_around} items={around} />

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <p className="text-sm">
          <span className="text-muted">Convenience: </span>
          <span className="font-semibold">{plan.convenience.rating}</span>
          <span className="text-muted"> · {plan.convenience.door_to_door_out} door to door</span>
        </p>
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          className={`rounded-full px-4 py-2 text-sm font-medium ${plan.recommended ? "bg-plum text-white" : "border border-line hover:border-plum-600"}`}
        >
          {open ? "Hide details" : "Explore plan"}
        </button>
      </div>

      {open && (
        <div className="mt-4 space-y-4 border-t border-line pt-4 text-sm">
          <p className="text-muted">{plan.convenience.summary}</p>
          <Timeline plan={plan} />
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">Where the numbers come from</p>
            <ul className="mt-1.5 space-y-1 text-xs text-muted">
              {[...new Set(plan.items.map((i) => `${i.source}${i.as_of ? ` (as of ${i.as_of.slice(0, 10)})` : ""}`))].map((s) => (
                <li key={s}>• {s}</li>
              ))}
              <li>
                • Rides are estimates with a likely range.{" "}
                {plan.items.some((i) => i.kind === "demo")
                  ? "Flight prices are demo fares, so the airline's real price will differ."
                  : "Flights and bag fees are quotes that can change until you book."}
              </li>
            </ul>
          </div>
          <div className="flex flex-wrap gap-2">
            {plan.booking_links.map((l) => (
              <a
                key={l.url}
                href={outboundUrl(l.url, tripId, plan.id, searchId)}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-full bg-plum px-4 py-2 text-sm font-medium text-white"
              >
                Continue on {l.label} ↗
              </a>
            ))}
            <button type="button" onClick={() => setReporting(!reporting)} className="text-xs text-plum underline">
              See a different price?
            </button>
          </div>
          {reporting && <PriceReport tripId={tripId} plan={plan} onDone={() => setReporting(false)} />}
        </div>
      )}
    </article>
  );
}

/** Show both flights as one row on the card; per-flight detail lives in "Explore plan". */
function mergeFlights(plan: Plan): LineItem[] {
  const flights = plan.items.filter((i) => i.category === "flight");
  const { outbound: out, return: back } = plan.flights;
  const total = flights.reduce((sum, i) => sum + i.likely, 0);
  const merged: LineItem = {
    ...flights[0],
    label: "Round-trip flights",
    detail: `${out.origin} ${out.depart_label} · back ${back.origin} ${back.depart_label} · ${out.fare_class}`,
    low: total,
    likely: total,
    high: total,
  };
  return [merged, ...plan.items.filter((i) => i.group === "bookings" && i.category !== "flight")];
}

function Section({ title, total, items }: { title: string; total: number; items: LineItem[] }) {
  return (
    <div className="mt-4">
      <div className="flex items-baseline justify-between border-b border-line pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
        <span>{title}</span>
        <span className="tabular-nums">{money(total)}</span>
      </div>
      <ul className="mt-2 space-y-2.5">
        {items.map((i) => (
          <li key={i.label} className="flex items-start justify-between gap-3 text-sm">
            <div className="min-w-0">
              <p className="font-medium leading-snug">
                <span aria-hidden className="mr-1.5 inline-block w-4 text-center text-xs">
                  {MODE_ICON[i.category] ?? "•"}
                </span>
                {i.label}
              </p>
              <p className="ml-[22px] text-xs leading-snug text-muted">{i.detail.split(" · leave ")[0]}</p>
            </div>
            <div className="shrink-0 text-right">
              <p className="font-semibold tabular-nums">
                {i.kind === "estimate" && <span className="mr-0.5 font-normal text-muted">~</span>}
                {money(i.likely)}
              </p>
              {i.kind === "estimate" && i.low !== i.high && (
                <p className="text-[10px] tabular-nums text-muted">
                  {money(i.low)}–{money(i.high)}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Timeline({ plan }: { plan: Plan }) {
  const { outbound: out, return: back } = plan.flights;
  const rows = [
    { day: "Going", steps: [plan.legs[0], { flight: out }, plan.legs[1]] },
    { day: "Coming home", steps: [plan.legs[2], { flight: back }, plan.legs[3]] },
  ] as const;
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {rows.map((r) => (
        <div key={r.day}>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">{r.day}</p>
          <ol className="mt-1.5 space-y-1.5 border-l-2 border-plum-100 pl-3">
            {r.steps.map((s, i) =>
              "flight" in s ? (
                <li key={i}>
                  <span className="tabular-nums text-muted">{s.flight.depart_label}</span> Flight {s.flight.origin} → {s.flight.destination}
                  <span className="text-muted"> · lands {s.flight.arrive_label}</span>
                </li>
              ) : (
                <li key={i}>
                  <span className="tabular-nums text-muted">{s.start_label}</span> {s.label}
                  <span className="text-muted">
                    {" "}
                    · {s.mode_label}, {s.minutes_low}–{s.minutes_high} min
                  </span>
                </li>
              ),
            )}
          </ol>
        </div>
      ))}
    </div>
  );
}

function PriceReport({ tripId, plan, onDone }: { tripId: string; plan: Plan; onDone: () => void }) {
  const [item, setItem] = useState(plan.items[0].label);
  const [observed, setObserved] = useState("");
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  const [failed, setFailed] = useState(false);
  const shown = plan.items.find((i) => i.label === item)?.likely;

  if (sent) return <p className="rounded-lg bg-plum-50 px-3 py-2 text-xs text-plum">Thanks, we&apos;ll check it. Reports like this keep our numbers honest.</p>;
  return (
    <form
      className="grid gap-2 rounded-lg border border-line p-3 text-xs sm:grid-cols-[1fr_110px]"
      onSubmit={async (e) => {
        e.preventDefault();
        setFailed(false);
        try {
          await api.reportPrice(tripId, {
            plan_id: plan.id,
            item_label: item,
            shown_price: shown,
            observed_price: observed ? Number(observed) : null,
            note: note || null,
          });
        } catch {
          setFailed(true);
          return;
        }
        setSent(true);
        setTimeout(onDone, 2500);
      }}
    >
      <label className="flex flex-col gap-1">
        Which price?
        <select value={item} onChange={(e) => setItem(e.target.value)} className="rounded-md border border-line bg-white px-2 py-1">
          {plan.items.map((i) => (
            <option key={i.label}>{i.label}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        You saw ($)
        <input type="number" min={0} step="0.01" value={observed} onChange={(e) => setObserved(e.target.value)} className="rounded-md border border-line bg-white px-2 py-1" />
      </label>
      <label className="flex flex-col gap-1 sm:col-span-2">
        Anything else? (optional)
        <input value={note} onChange={(e) => setNote(e.target.value)} className="rounded-md border border-line bg-white px-2 py-1" />
      </label>
      <button type="submit" className="w-fit rounded-full bg-plum px-3 py-1.5 font-medium text-white">
        Send report
      </button>
      {failed && (
        <p className="text-warn sm:col-span-2" role="alert">
          Couldn&apos;t send your report. Check the amount and try again.
        </p>
      )}
    </form>
  );
}
