"use client";

import { useState } from "react";
import { api } from "@/lib/api";

const ANSWERS = [
  { value: "yes", label: "Yes" },
  { value: "maybe", label: "Maybe" },
  { value: "no", label: "No" },
];

/** The concept test's main question, asked after a tester picks a plan (or none of them). */
export function ConceptQuestion({ tripId, searchId, planId }: { tripId: string; searchId: string; planId: string | null }) {
  const [answer, setAnswer] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  const [failed, setFailed] = useState(false);

  if (sent) {
    return <p className="rounded-lg bg-plum-50 px-3 py-2 text-sm text-plum" role="status">Thanks! Your answer helps us decide what to build next.</p>;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!answer) return;
    setFailed(false);
    try {
      await api.answerConcept(tripId, { search_id: searchId, plan_id: planId, would_change: answer, note: note.trim() || null });
      setSent(true);
    } catch {
      setFailed(true);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-line p-3 text-sm">
      <fieldset>
        <legend className="font-medium">Would seeing the door-to-door cost change which flight you book?</legend>
        <div className="mt-2 flex gap-4">
          {ANSWERS.map((a) => (
            <label key={a.value} className="flex items-center gap-1.5">
              <input type="radio" name={`concept-${planId ?? "none"}`} value={a.value} checked={answer === a.value} onChange={() => setAnswer(a.value)} />
              {a.label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="flex flex-col gap-1 text-xs">
        Why? (optional)
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000} className="rounded-md border border-line bg-white px-2 py-1.5 text-sm" />
      </label>
      <button type="submit" disabled={!answer} className="rounded-full bg-plum px-4 py-1.5 text-xs font-medium text-white disabled:opacity-40">
        Send answer
      </button>
      {failed && (
        <p className="text-xs text-warn" role="alert">
          Couldn&apos;t send your answer. Please try again.
        </p>
      )}
    </form>
  );
}
