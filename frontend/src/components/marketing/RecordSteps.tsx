import type { ReactNode } from "react";
import { Link } from "react-router";
import { RECORDS_PATH, type StepNo, type Tone } from "@/lib/recordWorks";

// 제작 기록 상세의 틀: 머리글, 만드는 순서 네 단계(접이식), 단계 안의 접는 칸.

const TONE: Record<Tone, string> = {
  ok: "bg-emerald-50 text-emerald-800",
  wait: "bg-amber-50 text-amber-800",
  bad: "bg-red-50 text-red-800",
  muted: "bg-ink-50 text-ink-700",
};

export function StatusChip({ text, tone }: { text: string; tone: Tone }) {
  return <span className={`chip ${TONE[tone]}`}>{text}</span>;
}

export function DetailHeader({ title, status, meta }: { title: string; status: { text: string; tone: Tone }; meta: string }) {
  return (
    <header className="space-y-2">
      <Link to={RECORDS_PATH} className="btn-ghost text-xs">‹ 제작 기록</Link>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="min-w-0 break-words text-xl font-bold tracking-tight">{title}</h1>
        <StatusChip {...status} />
        <span className="text-xs tabular-nums text-ink-500">{meta}</span>
      </div>
    </header>
  );
}

export function Step({ no, title, done, summary, open, locked = false, onToggle, children }: {
  no: StepNo; title: string; done: boolean; summary: string; open: boolean;
  /** 아직 갈 수 없는 단계: 한 줄만 보이고 펼치지 않는다 */
  locked?: boolean;
  onToggle: () => void; children?: ReactNode;
}) {
  const shown = open && !locked;
  return (
    <section className={`card ${shown ? "ring-2 ring-accent/40" : ""}`} aria-label={`${no} ${title}`}>
      <button type="button" className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-5 py-4 text-left disabled:cursor-default" aria-expanded={shown} disabled={locked} onClick={onToggle}>
        <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-semibold ${done ? "bg-emerald-50 text-emerald-800" : "bg-ink-50 text-ink-500"}`} aria-hidden="true">{done ? "✓" : no}</span>
        <span className={`w-32 shrink-0 text-sm font-semibold ${locked ? "text-ink-500" : "text-ink-900"}`}>{title}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-ink-500">{summary}</span>
        {!locked && <span className="shrink-0 text-xs text-ink-500">{shown ? "접기" : "펼치기"}</span>}
      </button>
      {shown && <div className="space-y-4 border-t border-ink-100 p-5">{children}</div>}
    </section>
  );
}

export function Fold({ title, open, children }: { title: string; open?: boolean; children: ReactNode }) {
  return (
    <details open={open}>
      <summary className="cursor-pointer text-sm text-ink-500">{title}</summary>
      <div className="space-y-3 pt-3">{children}</div>
    </details>
  );
}
