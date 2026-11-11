import { Link } from "@/lib/nav";
import { useEffect, useState } from "react";
import { Bars, Blockers, Loading, OFFLINE, Stat } from "@/components/admin/bits";
import { DeptUsage } from "@/components/admin/DeptUsage";
import { SupportBoard } from "@/components/support/SupportBoard";
import { ChartWithTable, Legend, LineChart, RingMeter, TableView } from "@/components/charts";
import { IconBolt, IconChevronRight } from "@/components/icons";
import { api } from "@/lib/api";
import { support, type InquiryPullStatus, type StuckIntakeItem } from "@/lib/support";
import { ago, pct, shortDate, shortDateTime, won } from "@/lib/format";
import { CATEGORY_KO, CLAIM_REASON_KO, CLAIM_TYPE_KO, RULE_KO } from "@/lib/labels";
import type { Dashboard } from "@/lib/types";

const GUARD_KO: Record<string, string> = {
  greeting: "인사말 없음", closing: "맺음 인사 없음", unsupported_promise: "근거 없는 약속", contradicts_decision: "판정과 모순된 약속",
  premature_promise: "확인 전 결과 약속", internal_info_leak: "내부 정보 노출", pii: "개인정보", absolute_wording: "단정적 표현",
  conditional_policy: "조건부 정책", review_bargaining: "후기 거래 제안", ungrounded_citation: "근거 없는 인용", length: "길이 초과",
  // 답변 가드(guard.py)가 실제로 내는 코드
  promise_refund: "환불·보상 단정", pii_in_reply: "개인정보 포함", link_in_reply: "링크 포함", too_long: "너무 긴 답변", empty_reply: "빈 답변",
  unsupported_language: "자동 검사 불가 언어", language_not_verified: "언어 확인 필요", no_evidence_reply: "근거 없는 답변",
};
// 클레임 사유(배송 지연 등)와 규칙 이름을 함께 풀어 쓴다: 실제 모드는 사유 코드를 보내서 영문 코드가 그대로 보이던 것을 고친다
const REASON_LABELS: Record<string, string> = { ...RULE_KO, ...CLAIM_REASON_KO };

const PULL_REFRESH_MS = 15_000;
const PULL_ERROR_KO: Record<string, string> = {
  UNAVAILABLE: "판매 서버에 연결할 수 없습니다", INGEST_FAILED: "읽어 온 문의를 처리하지 못한 건이 있습니다",
};

// 고객 문의는 판매 서버(문의함)에 저장되고 고객지원이 정해진 간격으로 읽어 옵니다. 못 읽으면 조용히 비지 않고 여기에 보입니다.
function InquiryPullBanner() {
  const [s, setS] = useState<InquiryPullStatus | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => support.inquiryPull().then((e) => { if (alive) setS(e.data); }).catch(() => { if (alive) setS(null); });
    void load();
    const t = setInterval(load, PULL_REFRESH_MS);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!s || !s.enabled) return null;
  const down = s.connected === false;
  const left = (s.pending ?? 0) + (s.claims_pending ?? 0);
  const stuck = (s.inbox_failed ?? 0) + (s.claims_inbox_failed ?? 0);
  const alert = down || stuck > 0;
  return (
    <>
    <p role={alert ? "alert" : "status"} className={`rounded-xl px-4 py-2 text-xs font-medium ${alert ? "bg-red-50 text-red-800" : "bg-surface text-ink-700 ring-1 ring-ink-100"}`}>
      {s.connected === null
        ? "판매 서버 문의함·클레임함을 아직 읽지 않았습니다."
        : down
          ? `판매 서버 문의·클레임 연결 끊김: ${PULL_ERROR_KO[s.last_error ?? ""] ?? s.last_error ?? "원인을 알 수 없습니다"}.${left ? ` 판매 서버에 문의 ${s.pending ?? 0}건·클레임 ${s.claims_pending ?? 0}건이 남아 있습니다.` : ""} 새 접수가 늦게 들어올 수 있습니다.`
          : `판매 서버 문의함·클레임함 연결 정상 · ${s.read_only ? "관찰 모드(읽기만 하고 판매 서버에는 쓰지 않습니다) · " : ""}${s.interval_seconds}초마다 읽습니다${left ? ` · 남은 문의 ${s.pending ?? 0}건·클레임 ${s.claims_pending ?? 0}건` : ""}.`}
      {stuck > 0 && ` 여러 번 처리에 실패해 따로 빼 둔 접수가 ${stuck}건 있습니다 (문의 ${s.inbox_failed ?? 0}·클레임 ${s.claims_inbox_failed ?? 0}). 아래 목록에서 확인하고 다시 시도해 주세요.`}
    </p>
    <StuckIntakeList count={stuck} />
    </>
  );
}

const STUCK_KIND_KO = { inquiry: "문의", claim: "클레임" } as const;

// 여러 번 읽지 못해 따로 빼 둔 접수: 놓치지 않게 번호·사유를 보여 주고, 사람이 확인한 뒤 다시 시도한다
function StuckIntakeList({ count }: { count: number }) {
  const [items, setItems] = useState<StuckIntakeItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = () => support.stuckIntake().then((e) => setItems(e.data.items)).catch(() => setItems([]));
  useEffect(() => { void load(); }, [count]);
  if (count === 0 && items.length === 0) return null;
  const retry = async (it: StuckIntakeItem) => {
    const key = `${it.kind}:${it.seq}`;
    setBusy(key); setNote(null);
    try {
      const r = await api.admin.retryStuckIntake(it.kind, it.seq);
      setNote(r.requeued ? `${STUCK_KIND_KO[it.kind]} #${it.seq}을 다시 읽는 차례로 돌렸습니다.` : `${STUCK_KIND_KO[it.kind]} #${it.seq}을 다시 올리지 못했습니다${r.error === "READ_ONLY" ? " — 읽기 전용(관찰 모드) 설치라 판매 서버를 바꾸지 않습니다" : r.error ? ` (${r.error})` : ""}.`);
      await load();
    } catch { setNote("다시 시도하지 못했습니다. 잠시 뒤 다시 눌러 주세요."); }
    finally { setBusy(null); }
  };
  return (
    <section aria-label="막힌 접수" className="card space-y-2 p-4">
      <h2 className="text-sm font-semibold">막힌 접수 {items.length}건</h2>
      <ul className="divide-y divide-ink-100 text-sm">
        {items.map((it) => (
          <li key={`${it.kind}:${it.seq}`} className="flex flex-wrap items-center gap-2 py-2">
            <span className="chip">{STUCK_KIND_KO[it.kind]}</span>
            <span className="font-medium">접수 번호 #{it.seq}</span>
            <span className="text-xs text-ink-500">사유 {PULL_ERROR_KO[it.reason ?? ""] ?? it.reason ?? "알 수 없음"} · {it.attempts}회 시도{it.failed_at ? ` · ${shortDateTime(it.failed_at)}` : ""}</span>
            <button type="button" className="btn-ghost ml-auto" disabled={busy !== null} onClick={() => void retry(it)}>
              {busy === `${it.kind}:${it.seq}` ? "다시 올리는 중…" : "다시 시도"}
            </button>
          </li>
        ))}
      </ul>
      {note && <p role="status" className="text-xs text-ink-700">{note}</p>}
    </section>
  );
}

export default function DashboardPage() {
  const [days, setDays] = useState(14);
  const [d, setD] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  // "분류 필요": 상담원이 원인 이름을 정해야 하는 문의. 목록의 표시를 그대로 센다(값을 지어내지 않는다)
  const [triage, setTriage] = useState<{ needed: number; done: number } | null>(null);
  useEffect(() => {
    let alive = true;
    api.admin.list().then((l) => {
      if (!alive) return;
      setTriage({ needed: l.filter((c) => c.triage?.state === "needed").length, done: l.filter((c) => c.triage?.state === "done").length });
    }).catch(() => { if (alive) setTriage(null); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { api.admin.dashboard(days).then((x) => { setD(x); setError(null); }).catch(() => setError(OFFLINE)); }, [days]);
  if (!d) return <Loading error={error} what="문의 현황을 불러오는 중…" />;
  const t = d.totals;
  const labels = d.daily.map((r) => shortDate(r.date));

  return (
    <div className="flex min-h-full flex-col xl:flex-row">
      <div className="min-w-0 flex-1 space-y-8 px-8 pb-8 pt-2">
        <InquiryPullBanner />

        <section aria-label="핵심 지표" className="flex flex-wrap gap-3">
          <Stat label="답변 대기 문의" value={t.open_waiting} sub={`긴급 ${t.high_waiting}건`} tone={t.high_waiting ? "text-accent" : ""} href="/console" />
          {triage && <Stat label="분류 필요 문의" value={triage.needed} sub={`원인 이름을 정할 문의 · 마친 ${triage.done}건`} tone={triage.needed ? "text-accent" : ""} href="/claims?view=triage" />}
          <Stat label="상담원 확인 대기 클레임" value={t.needs_review} href="/claims" />
          <Stat label="보상 큐 (준비/대기)" value={`${t.queue_ready} / ${t.queue_waiting}`} sub={`입고 대기 ${t.awaiting_return}건`} href="/queue" />
          <Stat label="자동 처리율" value={pct(t.auto_rate)} sub={`기간 내 클레임 ${t.claims}건`} />
        </section>

        <SupportBoard />

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
            chart={<LineChart height={220} area xLabels={labels} tooltipTitle={(i) => d.daily[i]?.date ?? ""}
              series={[{ key: "inq", label: "문의", color: "chart-1", values: d.daily.map((r) => r.inquiries) }, { key: "claims", label: "클레임", color: "chart-2", values: d.daily.map((r) => r.claims) }]} />}
            table={<TableView columns={["날짜", "문의", "클레임", "자동", "상담원 확인", "지급액"]}
              rows={[...d.daily].reverse().map((r) => [r.date, r.inquiries, r.claims, r.auto, r.agent, won(r.paid)])} />} />
          <p className="mt-2 text-[11px] text-ink-500">자동 = 상담원 없이 규칙만으로 끝난 클레임, 상담원 확인 = 한도 초과·영수증 불일치·검수 불일치 등으로 사람이 본 클레임. 지급액은 환불 현금과 보상 적립금의 합입니다.</p>
        </section>

        <section className="grid gap-6 md:grid-cols-2">
          <div>
            <h2 className="mb-3 text-base font-semibold">클레임 사유</h2>
            <Bars data={d.claim_reasons} labels={REASON_LABELS} />
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
