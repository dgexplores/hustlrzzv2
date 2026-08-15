import type { ReactNode } from "react";
import { cn } from "./button";

export function ScoreDial({ value, label, caption, className }: { value: number; label: string; caption?: string; className?: string }) {
  const score = Math.max(0, Math.min(Math.round(value) || 0, 100));
  return <div className={cn("relative grid h-28 w-28 shrink-0 place-items-center rounded-full", className)} style={{ background: `conic-gradient(hsl(var(--primary)) ${score * 3.6}deg, hsl(var(--secondary)) 0)` }} aria-label={`${label}: ${score} out of 100`}>
    <div className="grid h-[calc(100%-10px)] w-[calc(100%-10px)] place-items-center rounded-full bg-card text-center shadow-inner"><span><strong className="block text-2xl font-semibold tracking-[-0.06em]">{score}</strong><span className="block text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</span></span></div>
    {caption ? <span className="sr-only">{caption}</span> : null}
  </div>;
}

export function InsightSection({ eyebrow, title, description, children, className }: { eyebrow?: string; title: string; description?: string; children: ReactNode; className?: string }) {
  return <section className={cn("rounded-2xl border bg-card/70 p-4 sm:p-5", className)}>{eyebrow ? <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-primary">{eyebrow}</p> : null}<h3 className="mt-1 text-base font-semibold tracking-[-0.02em]">{title}</h3>{description ? <p className="mt-1 text-sm leading-6 text-muted-foreground">{description}</p> : null}<div className="mt-4">{children}</div></section>;
}

export function InsightTags({ items, tone = "neutral" }: { items?: string[]; tone?: "positive" | "warning" | "neutral" }) {
  const styles = tone === "positive" ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300" : tone === "warning" ? "border-amber-500/25 bg-amber-500/10 text-amber-900 dark:text-amber-200" : "border-border bg-secondary/50 text-secondary-foreground";
  return items?.length ? <div className="flex flex-wrap gap-2">{items.map((item) => <span key={item} className={cn("rounded-lg border px-2.5 py-1.5 text-sm", styles)}>{item}</span>)}</div> : <p className="text-sm text-muted-foreground">No specific items returned.</p>;
}
