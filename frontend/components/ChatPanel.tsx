"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/types";

interface Props {
  messages: ChatMessage[];
  onSend: (text: string) => Promise<void>;
  placeholder?: string;
  suggestions?: string[];
  title?: React.ReactNode;
  compact?: boolean;
  assistantMode?: "openai" | "offline";
}

export function ChatPanel({ messages, onSend, placeholder = "Tell me about your trip…", suggestions = [], title, compact, assistantMode }: Props) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending]);

  async function send(value: string) {
    const trimmed = value.trim();
    if (!trimmed || pending) return;
    setPending(trimmed);
    setText("");
    setError(null);
    try {
      await onSend(trimmed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setText(trimmed);
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {title && <div className="px-4 pt-4">{title}</div>}
      <div className={`flex-1 space-y-3 overflow-y-auto px-4 py-4 ${compact ? "text-[13px]" : "text-sm"}`} aria-live="polite">
        {messages.map((m) => (
          <Bubble key={m.id} role={m.role} text={m.content} />
        ))}
        {pending && (
          <>
            <Bubble role="user" text={pending} />
            <div className="w-fit rounded-2xl rounded-bl-sm bg-sand px-3 py-2 text-muted">
              <span className="animate-pulse">Thinking…</span>
            </div>
          </>
        )}
        <div ref={endRef} />
      </div>
      {error && <p className="px-4 pb-2 text-xs text-warn">{error}</p>}
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2 px-4 pb-3">
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => send(s)}
              className="rounded-full border border-line bg-paper px-3 py-1 text-xs text-ink hover:border-plum-600"
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <div className="flex items-center gap-2 rounded-full border border-line bg-paper py-1 pl-4 pr-1 focus-within:border-plum-600">
          <label htmlFor="chat-input" className="sr-only">
            Message the planner
          </label>
          <input
            id="chat-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={placeholder}
            className="min-w-0 flex-1 bg-transparent py-1.5 text-sm outline-none placeholder:text-muted"
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={!text.trim() || !!pending}
            aria-label="Send"
            className="grid h-8 w-8 place-items-center rounded-full bg-plum text-white disabled:opacity-40"
          >
            →
          </button>
        </div>
        {assistantMode === "offline" && (
          <p className="mt-1.5 px-2 text-[11px] text-muted">Offline demo assistant. Add an OpenAI key for full conversation.</p>
        )}
      </form>
    </div>
  );
}

function Bubble({ role, text }: { role: "user" | "assistant"; text: string }) {
  const mine = role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <p
        className={`max-w-[88%] whitespace-pre-line rounded-2xl px-3 py-2 leading-relaxed ${
          mine ? "rounded-br-sm bg-plum text-white" : "rounded-bl-sm bg-sand text-ink"
        }`}
      >
        {text}
      </p>
    </div>
  );
}
