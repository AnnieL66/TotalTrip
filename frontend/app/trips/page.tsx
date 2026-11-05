"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { recentTrips } from "@/lib/api";

export default function MyTrips() {
  const [trips, setTrips] = useState<ReturnType<typeof recentTrips>>([]);
  useEffect(() => setTrips(recentTrips()), []);

  return (
    <>
      <Header active="trips" />
      <main className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
        <h1 className="font-serif text-3xl">My trips</h1>
        <p className="mt-1 text-sm text-muted">Trips you started on this device.</p>
        {trips.length === 0 ? (
          <div className="mt-8 rounded-2xl border border-dashed border-line p-8 text-center">
            <p className="text-muted">No trips yet.</p>
            <Link href="/" className="mt-4 inline-block rounded-full bg-plum px-5 py-2 text-sm text-white">
              Plan a trip
            </Link>
          </div>
        ) : (
          <ul className="mt-6 divide-y divide-line rounded-2xl border border-line bg-paper">
            {trips.map((t) => (
              <li key={t.id}>
                <Link href={`/trip/${t.id}`} className="flex items-center justify-between gap-4 px-5 py-4 hover:bg-plum-50">
                  <span className="truncate">{t.label}</span>
                  <span className="shrink-0 text-xs text-muted">{new Date(t.at).toLocaleDateString()}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
}
