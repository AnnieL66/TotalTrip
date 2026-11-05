"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Brief, BriefField } from "@/lib/types";
import { Badge } from "./Badge";

const EDITABLE: Record<string, "place-nyc" | "place-chi" | "date" | "select" | "number" | "time"> = {
  origin: "place-nyc",
  destination: "place-chi",
  depart_date: "date",
  return_date: "date",
  carry_on: "select",
  checked_bags: "select",
  budget: "number",
  priority: "select",
  arrive_by: "time",
  will_drive: "select",
};

const OPTIONS: Record<string, { value: string; label: string }[]> = {
  carry_on: [
    { value: "1", label: "1 carry-on bag" },
    { value: "0", label: "Personal item only" },
  ],
  checked_bags: [
    { value: "0", label: "None" },
    { value: "1", label: "1 checked bag" },
    { value: "2", label: "2 checked bags" },
  ],
  priority: [
    { value: "cheapest", label: "Lowest total cost" },
    { value: "balanced", label: "Balance cost and comfort" },
    { value: "easiest", label: "Easiest trip, then lowest cost" },
  ],
  will_drive: [
    { value: "false", label: "Ride or transit" },
    { value: "true", label: "Drive and park" },
  ],
};

let placesCache: Promise<{ NYC: string[]; CHI: string[] }> | null = null;

export function BriefPanel({
  tripId,
  brief,
  onChange,
  compact = false,
}: {
  tripId: string;
  brief: Brief;
  onChange: (b: Brief, errors: string[]) => void;
  compact?: boolean;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [places, setPlaces] = useState<{ NYC: string[]; CHI: string[] }>({ NYC: [], CHI: [] });

  useEffect(() => {
    placesCache ??= api.places();
    placesCache.then(setPlaces).catch(() => undefined);
  }, []);

  async function save(name: string, raw: string) {
    setEditing(null);
    let value: unknown = raw;
    if (["carry_on", "checked_bags"].includes(name)) value = Number(raw);
    if (name === "budget") value = raw ? Number(raw) : null;
    if (name === "will_drive") value = raw === "true";
    if (name === "arrive_by") value = raw || null;
    try {
      const res = await api.editBrief(tripId, { [name]: value });
      onChange(res.brief, res.errors ?? []);
    } catch {
      onChange(brief, ["Couldn't save that change. Check your connection and try again."]);
    }
  }

  const visible = brief.fields.filter((f) => f.name !== "travelers" || !compact);

  return (
    <>
      <datalist id="places-NYC">{places.NYC.map((p) => <option key={p} value={p} />)}</datalist>
      <datalist id="places-CHI">{places.CHI.map((p) => <option key={p} value={p} />)}</datalist>
      <div className={compact ? "divide-y divide-line" : "grid gap-3 sm:grid-cols-2 lg:grid-cols-3"}>
        {visible.map((f) =>
          compact ? (
            <CompactRow key={f.name} field={f} />
          ) : (
            <FieldCard
              key={f.name}
              field={f}
              editing={editing === f.name}
              onEdit={() => EDITABLE[f.name] && setEditing(f.name)}
              onCancel={() => setEditing(null)}
              onSave={(v) => save(f.name, v)}
            />
          ),
        )}
      </div>
    </>
  );
}

function CompactRow({ field }: { field: BriefField }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-2 py-1.5 text-[13px]">
      <span className="text-muted">{field.label}</span>
      <span className={field.provenance === "assumed" ? "text-warn" : field.provenance === "missing" ? "text-muted italic" : ""}>
        {field.display ?? "Not set"}
        {field.provenance === "assumed" && <span className="sr-only"> (assumed)</span>}
      </span>
    </div>
  );
}

function FieldCard({
  field,
  editing,
  onEdit,
  onCancel,
  onSave,
}: {
  field: BriefField;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: (v: string) => void;
}) {
  const kind = EDITABLE[field.name];
  const initial = field.value === null || field.value === undefined ? "" : String(field.value);
  const [draft, setDraft] = useState(initial);
  useEffect(() => setDraft(kind?.startsWith("place") ? field.display ?? "" : initial), [editing, initial, field.display, kind]);

  const missing = field.provenance === "missing";
  return (
    <div
      className={`rounded-xl border px-3.5 py-3 ${
        missing && field.required ? "border-dashed border-plum-600/50 bg-plum-50" : "border-line bg-paper"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted">{field.label}</span>
        {missing && !field.required ? <Badge kind="neutral">Optional</Badge> : <Badge kind={field.provenance} />}
      </div>
      {editing && kind ? (
        <form
          className="mt-1.5 flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            onSave(draft);
          }}
        >
          {kind === "select" ? (
            <select
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="min-w-0 flex-1 rounded-md border border-line bg-white px-2 py-1 text-sm"
              aria-label={field.label}
            >
              {OPTIONS[field.name].map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              autoFocus
              type={kind === "date" ? "date" : kind === "number" ? "number" : kind === "time" ? "time" : "text"}
              list={kind === "place-nyc" ? "places-NYC" : kind === "place-chi" ? "places-CHI" : undefined}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={kind === "number" ? "e.g. 500" : undefined}
              className="min-w-0 flex-1 rounded-md border border-line bg-white px-2 py-1 text-sm"
              aria-label={field.label}
              onKeyDown={(e) => e.key === "Escape" && onCancel()}
            />
          )}
          <button type="submit" className="rounded-md bg-plum px-2.5 text-xs font-medium text-white">
            Save
          </button>
        </form>
      ) : (
        <button
          type="button"
          onClick={onEdit}
          disabled={!kind}
          className="mt-1 block w-full text-left text-sm font-medium enabled:hover:text-plum disabled:cursor-default"
          title={kind ? "Click to edit" : undefined}
        >
          {field.display ?? <span className="font-normal italic text-muted">Not set yet</span>}
        </button>
      )}
    </div>
  );
}
