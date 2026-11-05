const STYLES = {
  quote: "bg-quote-100 text-quote",
  estimate: "bg-ride-100 text-warn",
  user_said: "bg-plum-100 text-plum",
  assumed: "bg-warn-100 text-warn",
  missing: "bg-sand text-muted",
  neutral: "bg-sand text-ink",
  plum: "bg-plum text-white",
} as const;

const LABELS: Partial<Record<keyof typeof STYLES, string>> = {
  quote: "Quote",
  estimate: "Estimate",
  user_said: "You said",
  assumed: "I assumed",
  missing: "Needed",
};

export function Badge({ kind, children, title }: { kind: keyof typeof STYLES; children?: React.ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ${STYLES[kind]}`}>
      {children ?? LABELS[kind]}
    </span>
  );
}
