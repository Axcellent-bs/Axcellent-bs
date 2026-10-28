import type { ReactNode } from "react";
import { BLOCKER_KO, CLAIM_STATUS_KO, CLAIM_STATUS_TONE } from "@/lib/labels";

export function Stat({ label, value, sub, tone = "" }: { label: string; value: ReactNode; sub?: string; tone?: string }) {
  return (
    <div className="card flex min-w-[130px] flex-1 flex-col px-4 py-3 leading-tight">
      <span className="text-[11px] text-ink-500">{label}</span>
      <span className={`mt-0.5 text-xl font-semibold tabular-nums ${tone}`}>{value}</span>
      {sub && <span className="mt-0.5 text-[11px] text-ink-500">{sub}</span>}
    </div>
  );
}

export function StatusChip({ status }: { status: string }) {
  return <span className={`chip ${CLAIM_STATUS_TONE[status]}`}>{CLAIM_STATUS_KO[status] ?? status}</span>;
}

export function Blockers({ items }: { items: { code: string; message: string }[] }) {
  return (
    <ul className="space-y-1">
      {items.map((b, i) => (
        <li key={i} className="rounded-md bg-amber-50 px-2 py-1 text-xs leading-relaxed text-amber-800">
          <span className="mr-1 font-semibold">{BLOCKER_KO[b.code] ?? b.code}</span>{b.message}
        </li>
      ))}
    </ul>
  );
}

export function Loading({ error, what = "불러오는 중…" }: { error: string | null; what?: string }) {
  return <div className="flex h-64 items-center justify-center px-6 text-center text-sm text-ink-500">{error ?? what}</div>;
}

export const OFFLINE = "백엔드에 연결할 수 없습니다. backend 서버(포트 8000)가 실행 중인지 확인하세요.";

export function Bars({ data, labels, color = "bg-brand-500", max }: { data: Record<string, number>; labels?: Record<string, string>; color?: string; max?: number }) {
  const entries = Object.entries(data);
  const top = max ?? Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <p className="text-xs text-ink-500">데이터가 없습니다.</p>;
  return (
    <ul className="space-y-2">
      {entries.map(([k, v]) => (
        <li key={k} className="text-xs">
          <div className="mb-1 flex justify-between"><span>{labels?.[k] ?? k}</span><span className="tabular-nums text-ink-500">{v}</span></div>
          <div className="h-1.5 overflow-hidden rounded-full bg-ink-100"><div className={`h-full rounded-full ${color}`} style={{ width: `${(v / top) * 100}%` }} /></div>
        </li>
      ))}
    </ul>
  );
}
