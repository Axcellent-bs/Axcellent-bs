import { Link } from "@/lib/nav";
import { useEffect, useState } from "react";
import { Bars, Blockers, Loading, OFFLINE, Stat } from "@/components/admin/bits";
import { DeptUsage } from "@/components/admin/DeptUsage";
import { ChartWithTable, Legend, LineChart, RingMeter, TableView } from "@/components/charts";
import { IconBolt, IconChevronRight } from "@/components/icons";
import { api } from "@/lib/api";
import { ago, pct, shortDate, shortDateTime, won } from "@/lib/format";
import { CATEGORY_KO, CLAIM_REASON_KO, CLAIM_TYPE_KO, RULE_KO } from "@/lib/labels";
import type { Dashboard } from "@/lib/types";

const GUARD_KO: Record<string, string> = {
  greeting: "인사말 없음", closing: "맺음 인사 없음", unsupported_promise: "근거 없는 약속", contradicts_decision: "판정과 모순된 약속",
  premature_promise: "확인 전 결과 약속", internal_info_leak: "내부 정보 노출", pii: "개인정보", absolute_wording: "단정적 표현",
  conditional_policy: "조건부 정책", review_bargaining: "후기 거래 제안", ungrounded_citation: "근거 없는 인용", length: "길이 초과",
};

export default function DashboardPage() {
  const [days, setDays] = useState(14);
  const [d, setD] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.admin.dashboard(days).then((x) => { setD(x); setError(null); }).catch(() => setError(OFFLINE)); }, [days]);
  if (!d) return <Loading error={error} what="대시보드를 불러오는 중…" />;
  const t = d.totals;
  const labels = d.daily.map((r) => shortDate(r.date));

  return (
    <div className="flex min-h-full flex-col xl:flex-row">
      <div className="min-w-0 flex-1 space-y-8 px-8 pb-8 pt-2">
        <section aria-label="핵심 지표" className="flex flex-wrap gap-3">
          <Stat label="답변 대기 문의" value={t.open_waiting} sub={`긴급 ${t.high_waiting}건`} tone={t.high_waiting ? "text-accent" : ""} />
          <Stat label="상담원 확인 대기 클레임" value={t.needs_review} />
          <Stat label="보상 큐 (준비/대기)" value={`${t.queue_ready} / ${t.queue_waiting}`} sub={`입고 대기 ${t.awaiting_return}건`} />
          <Stat label="자동 처리율" value={pct(t.auto_rate)} sub={`기간 내 클레임 ${t.claims}건`} />
        </section>

        <section>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold">문의·클레임 추이</h1>
            <Legend items={[{ label: "문의", color: "chart-1" }, { label: "클레임", color: "chart-2" }]} />
            <div className="ml-auto flex gap-2 text-xs">
              {[7, 14, 30].map((n) => (
                <button key={n} onClick={() => setDays(n)} aria-pressed={days === n}
                  className={`rounded-full px-3 py-1.5 font-medium ${days === n ? "bg-accent text-white" : "bg-ink-50 text-ink-700 hover:bg-ink-100"}`}>{n === 7 ? "1주" : n === 14 ? "2주" : "1개월"}</button>
              ))}
            </div>
          </div>
          <ChartWithTable
            chart={<LineChart height={220} area xLabels={labels} tooltipTitle={(i) => d.daily[i].date}
              series={[{ key: "inq", label: "문의", color: "chart-1", values: d.daily.map((r) => r.inquiries) }, { key: "claims", label: "클레임", color: "chart-2", values: d.daily.map((r) => r.claims) }]} />}
            table={<TableView columns={["날짜", "문의", "클레임", "자동", "상담원 확인", "지급액"]}
              rows={[...d.daily].reverse().map((r) => [r.date, r.inquiries, r.claims, r.auto, r.agent, won(r.paid)])} />} />
          <p className="mt-2 text-[11px] text-ink-500">자동 = 상담원 없이 규칙만으로 끝난 클레임, 상담원 확인 = 한도 초과·영수증 불일치·검수 불일치 등으로 사람이 본 클레임. 지급액은 환불 현금과 보상 적립금의 합입니다.</p>
        </section>

        <section className="grid gap-6 md:grid-cols-2">
          <div>
            <h2 className="mb-3 text-base font-semibold">자주 걸리는 규칙</h2>
            <Bars data={d.claim_reasons} labels={RULE_KO} />
          </div>
          <div>
            <h2 className="mb-3 text-base font-semibold">문의 분류</h2>
            <Bars data={d.by_category} labels={CATEGORY_KO} color="bg-brand-600" />
            <h2 className="mb-3 mt-6 text-base font-semibold">답변 초안 가드 적발</h2>
            <Bars data={d.guard_findings} labels={GUARD_KO} color="bg-accent" />
          </div>
        </section>

        <DeptUsage />
      </div>

      <aside className="w-full shrink-0 space-y-8 bg-surface-2 px-8 py-6 xl:w-[400px]">
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">오늘의 브리핑</h2>
            <Link href="/console" aria-label="상담 콘솔 열기" className="text-ink-700"><IconChevronRight /></Link>
          </div>
          <div className="flex gap-4">
            <RingMeter value={t.auto_rate} size={72}><IconBolt size={20} /></RingMeter>
            <div className="text-sm">
              <p className="font-semibold">규칙이 자동 처리 {pct(t.auto_rate)}</p>
              <p className="mt-1 text-xs leading-relaxed text-ink-500">{t.agent_reviews ? `상담원이 ${t.agent_reviews}건을 직접 판단했고 그중 ${t.agent_overrides}건은 규칙 판정을 뒤집었습니다. 이 기록이 규칙을 고칠 근거가 됩니다.` : "상담원이 직접 판단한 건이 아직 없습니다. 승인·거절은 규칙 판정과 함께 기록되어 규칙을 고칠 근거가 됩니다."}</p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2 text-center text-xs">
            <div className="rounded-xl bg-surface p-3"><p className="text-ink-500">지급 환불</p><p className="mt-0.5 text-base font-semibold tabular-nums">{won(t.paid_cash)}</p></div>
            <div className="rounded-xl bg-surface p-3"><p className="text-ink-500">지급 적립금</p><p className="mt-0.5 text-base font-semibold tabular-nums">{won(t.paid_points)}</p></div>
          </div>
          {t.flagged_reviews > 0 && <Link href="/reviews" className="mt-3 block rounded-xl bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">보류된 후기 {t.flagged_reviews}건이 확인을 기다립니다 ›</Link>}
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between"><h2 className="text-base font-semibold">답변 대기 문의</h2><Link href="/console" className="text-xs text-ink-500">전체 ›</Link></div>
          <ul className="space-y-2">
            {d.inquiry_queue.map((q) => (
              <li key={q.id}>
                <Link href={`/console?id=${q.id}`} className="block rounded-xl bg-surface p-3 hover:ring-1 hover:ring-brand-500">
                  <div className="flex items-center gap-2 text-xs">
                    <span className={`h-2 w-2 rounded-full ${q.urgency === "high" ? "bg-red-500" : q.urgency === "medium" ? "bg-amber-400" : "bg-emerald-400"}`} />
                    <b>{q.customer_name}</b><span className="chip bg-brand-50 text-brand-700">{CATEGORY_KO[q.category ?? "other"]}</span>
                    <span className="ml-auto text-ink-500">{q.waiting_minutes >= 60 ? `${Math.round(q.waiting_minutes / 60)}시간` : `${q.waiting_minutes}분`} 대기</span>
                  </div>
                  <p className="mt-1 truncate text-sm font-medium">{q.subject}</p>
                </Link>
              </li>
            ))}
            {!d.inquiry_queue.length && <li className="text-xs text-ink-500">대기 중인 문의가 없습니다.</li>}
          </ul>
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between"><h2 className="text-base font-semibold">보상 큐 앞줄</h2><Link href="/queue" className="text-xs text-ink-500">전체 ›</Link></div>
          <ol className="space-y-2">
            {d.queue.map((e) => (
              <li key={e.claim_id} className="rounded-xl bg-surface p-3 text-xs">
                <div className="flex items-center gap-2"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-600 text-[10px] font-bold text-white">{e.position}</span>
                  <b>{e.customer}</b><span className="text-ink-500">{CLAIM_TYPE_KO[e.type]} · {CLAIM_REASON_KO[e.reason]}</span>
                  <span className={`chip ml-auto ${e.ready ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{e.ready ? "처리 가능" : "대기"}</span></div>
                <p className="mt-1 text-ink-500">입고 {shortDateTime(e.received_at)} ({ago(e.received_at)})</p>
                {!e.ready && <div className="mt-1.5"><Blockers items={e.blockers} /></div>}
              </li>
            ))}
            {!d.queue.length && <li className="text-xs text-ink-500">입고된 반품이 없습니다.</li>}
          </ol>
        </section>
      </aside>
    </div>
  );
}
