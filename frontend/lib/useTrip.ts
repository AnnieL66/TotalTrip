"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { TripState } from "./types";

export function useTrip(id: string) {
  const [trip, setTrip] = useState<TripState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getTrip(id)
      .then((t) => alive && setTrip(t))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Couldn't load this trip."));
    return () => {
      alive = false;
    };
  }, [id]);

  const send = useCallback(
    async (text: string) => {
      const next = await api.sendMessage(id, text);
      setTrip(next);
      return next;
    },
    [id],
  );

  return { trip, setTrip, error, send };
}
