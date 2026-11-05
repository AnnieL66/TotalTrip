"use client";

import { Header } from "@/components/Header";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-md p-10 text-center">
        <h1 className="font-serif text-2xl">Something went wrong</h1>
        <p className="mt-2 text-sm text-muted">Please try again in a moment.</p>
        <button type="button" onClick={reset} className="mt-6 rounded-full bg-plum px-5 py-2 text-sm text-white">
          Try again
        </button>
      </main>
    </>
  );
}
