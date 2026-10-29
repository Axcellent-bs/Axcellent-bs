import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { CATEGORY_KO, STATUS_KO } from "@/lib/labels";
import { Link } from "@/lib/nav";
import { TOPIC_KO, ko, support, type InquirySurge } from "@/lib/support";
import type { ConversationListItem, Kpi } from "@/lib/types";

// 총괄 화면에서 보는 고객지원 현황: 문의 건수·상태·분류별. 읽기 전용이다.
// 값은 고객지원이 계산해 준 것(`/kpis`의 건수·상태)과 문의 목록(`/conversations`의 분류)이고, 화면이 지어내지 않는다.
// 고객지원 서비스가 꺼져 있으면 숫자 대신 "확인할 수 없음"을 보인다.
const POLL_MS = 10_000;
const STATUS_ORDER = ["open", "escalated", "resolved"] as const;
const STATUS_TONE: Record<string, string> = { open: "text-brand-700", escalated: "text-amber-800", resolved: "text-emerald-800" };

function Stat({ label, value, tone = "" }: { label: string; value: number; tone?: string }) {
  return (
    <div className="min-w-[88px]">
      <dt className="text-xs text-ink-500">{label}</dt>
      <dd className={`text-2xl font-semibold tabular-nums ${tone}`}>{value}<span className="ml-0.5 text-sm font-medium">건</span></dd>
    </div>
  );
}

export function SupportOverviewCard() {
  const [kpi, setKpi] = useState<Kpi | null | undefined>(undefined); // undefined = 아직 불러오는 중, null = 확인할 수 없음
  const [items, setItems] = useState<ConversationListItem[] | null>(null);
  const [surge, setSurge] = useState<InquirySurge | null>(null);
  const [surgeFailed, setSurgeFailed] = useState(false); // 급증이 없는 것과 급증을 못 읽은 것을 구분한다
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [k, l, s] = await Promise.allSettled([api.admin.kpi(), api.admin.list(), support.inquirySurge()]);
    if (k.status === "fulfilled") setKpi(k.value ?? null);
    if (l.status === "fulfilled") setItems(l.value);
    if (s.status === "fulfilled") { setSurge(s.value.data); setSurgeFailed(false); } else setSurgeFailed(true);
    setError(k.status === "rejected" && l.status === "rejected" ? "고객지원 현황을 불러오지 못했습니다." : null);
  }, []);
  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const byCategory = new Map<string, number>();
  for (const i of items ?? []) byCategory.set(i.category ?? "none", (byCategory.get(i.category ?? "none") ?? 0) + 1);
  const categories = [...byCategory.entries()].sort((a, b) => b[1] - a[1]);
  const maxCount = Math.max(1, ...categories.map(([, n]) => n));
  // 답변 대기는 상담 콘솔과 같은 기준이다: 해결·이관된 문의는 마지막 말이 고객이어도 세지 않는다
  const waiting = (items ?? []).filter((i) => i.status === "open" && i.awaiting_reply).length;
  const loading = kpi === undefined && items === null && !error;

  return (
    <section aria-labelledby="orch-support" className="rounded-[28px] bg-surface p-6 ring-1 ring-ink-100">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="orch-support" className="text-sm font-semibold text-ink-900">고객지원 현황</h2>
        <span className="text-xs text-ink-500">문의 건수·상태·분류별, 급증 경고 · 10초마다 갱신</span>
        <Link href="/console" className="btn-ghost ml-auto px-3 py-1 text-xs">상담 콘솔 열기</Link>
      </div>

      {surge && surge.surges.length > 0 && (
        <div role="alert" className="mt-4 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <p className="font-semibold">문의 급증 · 최근 {surge.window_minutes}분에 평소보다 많이 들어온 분류가 {surge.surges.length}개 있습니다</p>
          <ul className="mt-2 space-y-1.5">
            {surge.surges.map((x) => (
              <li key={x.topic_code} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <b>{ko(TOPIC_KO, x.topic_code)}</b>
                <span className="tabular-nums">최근 {x.count}건</span>
                <span className="chip bg-white/70 text-red-700">{x.ratio == null ? "평소 없음" : `평소의 ${x.ratio}배`}</span>
                <span className="ml-auto flex flex-wrap gap-2 text-xs">
                  {x.sample_inquiry_ids.map((i) => <Link key={i} href={`/support?inquiry=${i}#inquiry`} className="underline">문의 #{i}</Link>)}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] opacity-80">최근 {surge.window_minutes}분 문의를 지난 {surge.baseline_hours}시간의 같은 길이 평균과 견준 결과입니다. 급증이 없으면 이 경고는 나타나지 않습니다.</p>
        </div>
      )}
      {surgeFailed && <p className="mt-3 text-[11px] text-ink-500">문의 급증 감지를 불러오지 못했습니다. 급증이 없다는 뜻은 아닙니다.</p>}

      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      {loading && <p className="mt-4 text-sm text-ink-500">불러오는 중…</p>}

      {kpi === null && !error && (
        <p className="mt-4 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">확인할 수 없음 · 고객지원 서비스가 꺼져 있어 문의 건수를 가져오지 못했습니다.</p>
      )}

      {kpi && (
        <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-3">
          <Stat label="전체 문의" value={kpi.conversations} />
          {STATUS_ORDER.map((s) => <Stat key={s} label={STATUS_KO[s]} value={kpi.by_status[s] ?? 0} tone={STATUS_TONE[s]} />)}
          {items && <Stat label="답변 대기" value={waiting} tone={waiting ? "text-red-700" : ""} />}
        </dl>
      )}

      {items && (
        <div className="mt-5">
          <h3 className="text-xs font-medium text-ink-500">분류별 문의</h3>
          {categories.length === 0 ? (
            <p className="mt-2 text-sm text-ink-500">아직 접수된 문의가 없습니다.</p>
          ) : (
            <ul className="mt-2 space-y-1.5" aria-label="분류별 문의 건수">
              {categories.map(([cat, n]) => (
                <li key={cat} className="flex items-center gap-3 text-xs">
                  <span className="w-24 shrink-0 truncate text-ink-700">{cat === "none" ? "분류 전" : CATEGORY_KO[cat] ?? cat}</span>
                  <span className="h-2 flex-1 overflow-hidden rounded-full bg-ink-100">
                    <span className="block h-full rounded-full bg-brand-500" style={{ width: `${(n / maxCount) * 100}%` }} />
                  </span>
                  <span className="w-10 shrink-0 text-right tabular-nums font-medium">{n}건</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
