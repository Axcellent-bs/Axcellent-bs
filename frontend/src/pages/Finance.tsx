import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuditSection } from "@/components/finance/Audit";
import { ClosingSection } from "@/components/finance/Closing";
import { Chip, ErrorBox, MetaLine, MockBadge, TONE, Waiting, useResource } from "@/components/finance/bits";
import { IncidentFinance } from "@/components/finance/Incident";
import { SelfTestPanel } from "@/components/finance/Kept";
import { PolicySection } from "@/components/finance/Policy";
import { ReconciliationSection } from "@/components/finance/Reconciliation";
import { SafetyQueue } from "@/components/finance/SafetyQueue";
import { SettlementsSection } from "@/components/finance/Settlements";
import { Link } from "@/lib/nav";
import {
  FINANCE_SERVER, FINANCE_TABS, SAFETY_SERVER, finance, incidentName, krw, plainText, sortIncidents, todoTab,
  type FinanceKpis, type FinanceTab, type FinanceTodo, type Review, type SelfTest, type SelfTestLastMeta,
} from "@/lib/finance";

// 재무 AI 화면 = 캣북스 재무팀. 기본 화면은 시나리오 A(결제 장애)의 "장애 사건 재무"(⑨ 쿠폰 장부 → ⑩ 손실 → ⑰ 사건 후 감사).
// 평상시 업무(결산·대사·정산·한도 제안·감사)는 두 번째 탭에 둔다. 재무 AI 테스트는 서버가 10분마다 자동으로 한다.
// 건별 금액 승인은 하지 않는다(건별 검사는 안전 장치). 숫자는 서버가 계산한 값을 그대로 보여 주고, 말만 쉽게 바꾼다.
// 기준: ax-backend docs/notes/finance/2026-10-23-finance-redesign.md §8·§11·§12

const REFRESH_MS = 30_000;
/** 재무 AI 테스트 최근 결과를 다시 받는 간격 */
const SELF_TEST_POLL_MS = 60_000;

const TAB_LABEL: Record<FinanceTab, string> = {
  closing: "① 결산·리포트",
  reconciliation: "② 매출·PG 대사",
  settlements: "③ 판매자 정산",
  policy: "⑤ 예산·한도",
  audit: "⑦ 내부통제·감사",
};

const KPI: { key: keyof FinanceKpis; label: string; hint: string }[] = [
  { key: "gmv", label: "총거래액", hint: "고객이 결제한 금액 전체" },
  { key: "commission_revenue", label: "판매 수수료 매출", hint: "판매자에게 받는 수수료. 캣북스의 매출" },
  { key: "refund_total", label: "환불", hint: "고객에게 돌려준 돈" },
  { key: "coupon_cost", label: "쿠폰 비용", hint: "회사가 부담한 쿠폰 할인" },
  { key: "pg_fee", label: "PG 수수료", hint: "결제대행사에 내는 수수료" },
  { key: "net_result", label: "영업 결과", hint: "수수료 매출에서 비용을 뺀 값" },
];

const TODO_TONE: Record<FinanceTodo["kind"], string> = {
  settlement_batch: TONE.info, closing_confirm: TONE.info, recon_exception: TONE.bad, audit_finding: TONE.warn, policy_alert: TONE.warn,
};
const TODO_KO: Record<FinanceTodo["kind"], string> = {
  settlement_batch: "판매자 정산", closing_confirm: "결산", recon_exception: "대사", audit_finding: "감사", policy_alert: "한도",
};

/** 한쪽 서버가 꺼져 있어도 나머지는 보이도록 따로 받는다 */
async function settle<T>(p: Promise<T>): Promise<[T | null, string | null]> {
  try { return [await p, null]; } catch (e) { return [null, (e as Error).message]; }
}

/** 화면 맨 위 전환: 장애 사건 재무(기본) | 평상시 재무 업무. 주소가 #daily 또는 #closing 같은 모듈 이름이면 평상시 업무 */
type Mode = "incident" | "daily";
const MODE_LABEL: Record<Mode, string> = { incident: "장애 사건 재무", daily: "평상시 재무 업무" };

const scrollToId = (id: string) => requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }));

export default function FinancePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const hashTab = location.hash.replace(/^#/, "");
  const isTab = (FINANCE_TABS as string[]).includes(hashTab);
  const mode: Mode = isTab || hashTab === "daily" ? "daily" : "incident";
  const tab: FinanceTab = isTab ? (hashTab as FinanceTab) : "closing";
  const urlIncident = new URLSearchParams(location.search).get("incident");
  const openTab = useCallback((t: FinanceTab, scroll = false) => {
    navigate({ search: location.search, hash: `#${t}` }, { replace: true });
    if (scroll) scrollToId("finance-modules");
  }, [navigate, location.search]);
  const openMode = useCallback((m: Mode) => {
    navigate({ search: location.search, hash: m === "daily" ? "#daily" : "" }, { replace: true });
  }, [navigate, location.search]);
  /** 사건 고르기: 주소(?incident=)에 남겨 새로고침해도 같은 사건을 본다 */
  const openIncident = useCallback((id: string, scroll = false) => {
    navigate({ search: `?incident=${encodeURIComponent(id)}`, hash: "" }, { replace: true });
    if (scroll) scrollToId("incident-finance");
  }, [navigate]);

  // 30초마다, 그리고 무언가를 바꾼 뒤 모든 섹션을 새로 받는다
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    const t = setInterval(bump, REFRESH_MS);
    return () => clearInterval(t);
  }, [bump]);

  const overview = useResource("overview", () => finance.overview(), tick);
  const incidents = useResource("incidents", () => finance.incidents(), tick);
  const incidentList = sortIncidents(incidents.env?.data ?? []);
  const selectedIncident = urlIncident ?? incidentList[0]?.context_id ?? null;

  // 서버 상태 · 보류 큐 · 손실
  const [financeUp, setFinanceUp] = useState<boolean | null>(null);
  const [safetyUp, setSafetyUp] = useState<boolean | null>(null);
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [reviewsError, setReviewsError] = useState<string | null>(null);

  const loadSide = useCallback(async () => {
    const [[fh], [sh], [rv, rvErr]] = await Promise.all([
      settle(finance.financeHealth()), settle(finance.safetyHealth()), settle(finance.reviews()),
    ]);
    setFinanceUp(fh != null);
    setSafetyUp(sh != null);
    if (rv) setReviews(rv);
    setReviewsError(rvErr);
  }, []);
  useEffect(() => { void loadSide(); }, [loadSide, tick]);

  // 재무 AI 테스트: 서버가 10분마다 자동으로 하고, 화면은 1분마다 최근 결과를 받는다
  const [test, setTest] = useState<SelfTest | null>(null);
  const [testMeta, setTestMeta] = useState<SelfTestLastMeta | null>(null);
  const [testLoaded, setTestLoaded] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadLastTest = useCallback(async () => {
    try {
      const res = await finance.lastSelfTest();
      setTest(res.data ?? null);
      setTestMeta(res.meta ?? null);
    } catch { /* 서버가 꺼져 있으면 위의 연결 안내가 대신 보인다 */ }
    finally { setTestLoaded(true); }
  }, []);
  useEffect(() => {
    void loadLastTest();
    const t = setInterval(() => void loadLastTest(), SELF_TEST_POLL_MS);
    return () => clearInterval(t);
  }, [loadLastTest]);
  async function runTest() {
    setTesting(true);
    setError(null);
    try { setTest(await finance.selfTest()); setTestOpen(true); void loadLastTest(); }
    catch { setError(`재무 AI 테스트를 실행하지 못했습니다. ${FINANCE_SERVER}가 켜져 있는지 확인하세요.`); }
    finally { setTesting(false); }
  }

  // 정산 묶음 요청 뒤 보류 큐로 이동
  const [highlight, setHighlight] = useState<string | null>(null);
  const goToQueue = useCallback((reviewId: string | null) => {
    setHighlight(reviewId);
    void loadSide().then(() => scrollToId("safety-queue"));
  }, [loadSide]);

  const pendingReviews = (reviews ?? []).filter((r) => r.status === "pending");
  const todo = overview.env?.data.todo ?? [];
  // 장애 사건: 보류 쿠폰이 있거나 사건 후 감사에서 문제가 나온 사건
  const heldIncidents = incidentList.filter((i) => i.held_count > 0);
  const failedIncidents = incidentList.filter((i) => i.checks_ok === false);
  // 보류 쿠폰으로 이미 보여 준 사건의 보류 건은 "보류 큐" 항목에서 빼 두 번 세지 않는다
  const heldCtx = new Set(heldIncidents.map((i) => i.context_id));
  const otherPending = pendingReviews.filter((r) => !heldCtx.has(r.context_id));
  const todoCount = todo.length + (otherPending.length ? 1 : 0) + heldIncidents.length + failedIncidents.length;
  const anyTodoLoaded = overview.env || reviews || incidents.env;

  return (
    <div className="w-full space-y-6 px-4 pb-12 pt-2 sm:space-y-8 sm:px-8">
      {/* 머리말 */}
      <header className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">재무 관리</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">
            캣북스의 재무팀입니다. 장애가 나면 보상 쿠폰을 사건별 장부에 적고, 피해와 보상을 더해 손실을 정리하고, 끝난 뒤 보상이 규칙대로 나갔는지 감사합니다.
            평소에는 결산·대사·정산·한도·감사를 합니다. 쿠폰·환불을 한 건씩 승인하지는 않습니다(그건 안전 장치가 기준표로 검사합니다).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs sm:ml-auto">
          <button className="btn-ghost" onClick={bump}>새로고침</button>
          {safetyUp != null && <Chip label={safetyUp ? "안전 장치 켜짐" : "안전 장치 꺼짐"} tone={safetyUp ? TONE.ok : TONE.bad} />}
          {financeUp != null && <Chip label={financeUp ? "재무 AI 켜짐" : "재무 AI 꺼짐"} tone={financeUp ? TONE.ok : TONE.bad} />}
        </div>
      </header>

      <details className="rounded-2xl border border-ink-100 bg-surface px-4 py-3 text-sm sm:px-5">
        <summary className="cursor-pointer font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">처음이라면: 관리자가 할 일</summary>
        <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-ink-700">
          <li><b>재무 AI 테스트 확인</b>: 맨 위 <b>재무 AI 테스트</b>가 초록색(정상)인지 봅니다. 서버가 10분마다 스스로 테스트하므로 누를 필요는 없습니다. "늦어지고 있습니다" 경고가 보이면 재무 AI 서버를 확인합니다.</li>
          <li><b>사건 고르기</b>: <b>장애 사건 재무</b> 탭 위쪽 사건 탭에서 살펴볼 사건을 고릅니다. 가장 최근 사건이 먼저 열려 있고, 오래된 사건은 끝의 <b>이전 사건</b> 목록에 있습니다.</li>
          <li><b>손실 확인</b>: 피해(데이터 AI가 센 숫자), 보상 쿠폰, <b>총 손실 = 피해 + 보상</b>, 보상 한도를 얼마나 썼는지 봅니다. 재무 단계 ⑨·⑩이 <b>끝남</b>이면 장부와 손실 리포트가 준비된 것입니다.</li>
          <li><b>감사 결과 확인</b>: ⑰ 사건 후 감사에 ✗가 있으면 관련 기록 번호를 재무 담당자에게 알립니다. 영향 고객이 아닌 사람이 쿠폰을 받았거나, 한 사람이 두 번 받았거나, 한도를 넘었다는 뜻입니다.</li>
          <li><b>보류 큐 결정</b>: 판정이 <b>C</b>인 쿠폰은 아래 <b>안전 장치 보류 큐</b>에서 진행 또는 멈춤을 누릅니다. 10분 안에 아무도 정하지 않으면 자동으로 취소됩니다.</li>
          <li><b>평상시 업무</b>: <b>평상시 재무 업무</b> 탭에서 결산 확정, 대사 메모, 판매자 정산 묶음 요청, 한도 제안 확인, 감사를 합니다.</li>
        </ol>
        <p className="mt-3 text-xs text-ink-500">
          단계별 설명과 자주 묻는 질문은 <Link href="/finance/guide" className="font-medium text-brand-700 underline">재무 AI 관리자 사용법</Link>에 있습니다.
        </p>
      </details>

      <SelfTestPanel test={test} meta={testMeta} loaded={testLoaded} open={testOpen} running={testing} disabled={financeUp === false} onRunNow={runTest} />

      {(financeUp === false || safetyUp === false) && (
        <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
          {financeUp === false && `${FINANCE_SERVER}에 연결하지 못했습니다. `}
          {safetyUp === false && `${SAFETY_SERVER}에 연결하지 못했습니다(꺼져 있는 동안 돈이 나가는 일은 모두 멈춥니다). `}
          ax-backend에서 <code>docker compose -f infra/compose.yml up -d safety finance</code>로 켜 주세요.
        </p>
      )}
      {error && <ErrorBox message={error} />}

      {/* 지금 사람이 할 일 */}
      <section aria-labelledby="todo" className={`rounded-3xl p-4 ring-1 sm:p-6 ${todoCount ? "bg-amber-50 ring-amber-100" : "bg-emerald-50 ring-emerald-100"}`}>
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h2 id="todo" className={`text-sm font-semibold ${todoCount ? "text-amber-800" : "text-emerald-800"}`}>지금 사람이 할 일</h2>
          <p className={`text-3xl font-bold tabular-nums ${todoCount ? "text-amber-800" : "text-emerald-800"}`}>{anyTodoLoaded ? (todoCount ? `${todoCount}가지` : "없음") : "–"}</p>
          <MockBadge meta={overview.env?.meta} />
        </div>
        {overview.error && !overview.env && <div className="mt-3"><ErrorBox message={overview.error} onRetry={overview.reload} /></div>}
        {!overview.env && !overview.error && <Waiting />}
        {todoCount > 0 && (
          <ul className="mt-4 space-y-2">
            {heldIncidents.map((i) => (
              <li key={`held-${i.context_id}`}>
                <button type="button" onClick={() => goToQueue(null)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl bg-surface px-4 py-3 text-left shadow-sm transition hover:ring-2 hover:ring-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                  <Chip label="보류 쿠폰" tone={TONE.warn} />
                  <span className="min-w-0 flex-1 text-sm font-medium text-ink-900">{incidentName(i.context_id, i.component)} 보상 쿠폰 {i.held_count}장이 보류 큐에서 결정을 기다림</span>
                  <span className="text-xs font-medium text-brand-700">보류 큐로 →</span>
                </button>
              </li>
            ))}
            {failedIncidents.map((i) => (
              <li key={`audit-${i.context_id}`}>
                <button type="button" onClick={() => openIncident(i.context_id, true)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl bg-surface px-4 py-3 text-left shadow-sm transition hover:ring-2 hover:ring-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                  <Chip label="사건 감사" tone={TONE.bad} />
                  <span className="min-w-0 flex-1 text-sm font-medium text-ink-900">{incidentName(i.context_id, i.component)} 사건 후 감사에서 확인할 점이 나옴</span>
                  <span className="text-xs font-medium text-brand-700">보러 가기 →</span>
                </button>
              </li>
            ))}
            {todo.map((t, i) => (
              <li key={`${t.kind}-${i}`}>
                <button type="button" onClick={() => openTab(todoTab(t), true)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl bg-surface px-4 py-3 text-left shadow-sm transition hover:ring-2 hover:ring-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                  <Chip label={TODO_KO[t.kind] ?? "할 일"} tone={TODO_TONE[t.kind] ?? TONE.muted} />
                  <span className="min-w-0 flex-1 text-sm font-medium text-ink-900">{plainText(t.title)}</span>
                  {t.amount != null && <span className="text-sm tabular-nums text-ink-700">{krw(t.amount)}</span>}
                  <span className="text-xs font-medium text-brand-700">보러 가기 →</span>
                </button>
              </li>
            ))}
            {otherPending.length > 0 && (
              <li>
                <button type="button" onClick={() => scrollToId("safety-queue")}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl bg-surface px-4 py-3 text-left shadow-sm transition hover:ring-2 hover:ring-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                  <Chip label="보류 큐" tone={TONE.warn} />
                  <span className="min-w-0 flex-1 text-sm font-medium text-ink-900">안전 장치 보류 큐에서 결정 기다림 {otherPending.length}건</span>
                  <span className="text-xs font-medium text-brand-700">보러 가기 →</span>
                </button>
              </li>
            )}
          </ul>
        )}
        {overview.env && todoCount === 0 && <p className="mt-2 text-sm text-emerald-800">재무 AI가 맞춰 본 결과, 지금 사람이 볼 예외가 없습니다.</p>}
        {incidents.error && !incidents.env && <p className="mt-2 text-xs text-amber-800">장애 사건 목록을 받지 못해 보류 쿠폰·사건 감사 항목은 빠져 있을 수 있습니다.</p>}
      </section>

      {/* 맨 위 전환: 장애 사건 재무 | 평상시 재무 업무 */}
      <div role="tablist" aria-label="재무 화면" className="flex w-full gap-1 rounded-2xl bg-surface p-1 ring-1 ring-ink-100 sm:w-auto sm:max-w-md">
        {(["incident", "daily"] as Mode[]).map((m) => (
          <button key={m} type="button" role="tab" id={`mode-${m}`} aria-selected={mode === m} aria-controls={`mode-panel-${m}`} onClick={() => openMode(m)}
            className={`flex-1 rounded-xl px-4 py-2.5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${mode === m ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
            {MODE_LABEL[m]}
            {m === "incident" && mode !== m && heldIncidents.length + failedIncidents.length > 0 && <span className="ml-1.5 inline-block h-2 w-2 rounded-full bg-amber-500 align-middle" role="img" aria-label="확인할 일 있음" />}
          </button>
        ))}
      </div>

      {mode === "incident" && (
        <div role="tabpanel" id="mode-panel-incident" aria-labelledby="mode-incident">
          <ErrorBoundary title="사건 화면을 그리지 못했습니다">
            <IncidentFinance list={incidents} selected={selectedIncident} onSelect={(id) => openIncident(id)} tick={tick} onGoToQueue={goToQueue} />
          </ErrorBoundary>
        </div>
      )}

      {mode === "daily" && (
      <div role="tabpanel" id="mode-panel-daily" aria-labelledby="mode-daily" className="space-y-6 sm:space-y-8">
      {/* 오늘의 재무 */}
      <section aria-labelledby="today" className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="today" className="text-base font-semibold text-ink-900">오늘의 재무</h2>
          {overview.env && <span className="text-xs text-ink-500">{overview.env.data.date}</span>}
          <MockBadge meta={overview.env?.meta} />
        </div>
        {overview.env ? (
          <>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              {KPI.map(({ key, label, hint }) => {
                const v = overview.env!.data.kpis[key];
                return (
                  <div key={key} className={`card px-4 py-3 ${key === "net_result" ? "ring-1 ring-brand-100" : ""}`}>
                    <dt className="text-[11px] text-ink-500">{label}</dt>
                    <dd className={`mt-0.5 break-words font-semibold tabular-nums ${v == null ? "text-sm text-ink-500" : `text-lg sm:text-xl ${v < 0 ? "text-red-700" : "text-ink-900"}`}`}>
                      {v != null && v < 0 ? `−${krw(-v)}` : krw(v)}
                    </dd>
                    <dd className="mt-0.5 text-[11px] leading-snug text-ink-500">{hint}</dd>
                  </div>
                );
              })}
            </dl>
            <MetaLine meta={overview.env.meta} />
          </>
        ) : overview.error ? <ErrorBox message={overview.error} onRetry={overview.reload} /> : <Waiting />}
      </section>

      {/* 재무팀 5개 모듈 */}
      <div id="finance-modules" className="scroll-mt-4 space-y-4">
        <div role="tablist" aria-label="재무팀 업무" className="scroll-thin -mx-1 flex gap-1 overflow-x-auto px-1 pb-1">
          {FINANCE_TABS.map((t) => (
            <button key={t} type="button" role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls={`panel-${t}`} onClick={() => openTab(t)}
              className={`shrink-0 rounded-full px-4 py-2 text-sm font-medium transition ${tab === t ? "bg-brand-600 text-white" : "bg-surface text-ink-700 hover:bg-ink-100"}`}>
              {TAB_LABEL[t]}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "closing" && <ClosingSection tick={tick} onChanged={bump} />}
          {tab === "reconciliation" && <ReconciliationSection tick={tick} />}
          {tab === "settlements" && <SettlementsSection tick={tick} onChanged={bump} onGoToQueue={goToQueue} />}
          {tab === "policy" && <PolicySection tick={tick} />}
          {tab === "audit" && <AuditSection tick={tick} />}
        </div>
      </div>

      </div>
      )}

      <SafetyQueue reviews={reviews} error={reviewsError} highlight={highlight} reload={loadSide} />
    </div>
  );
}
