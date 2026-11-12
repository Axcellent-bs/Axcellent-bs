import { useEffect, useRef, useState, type ReactNode } from "react";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import { won } from "@/lib/format";
import {
  DATA_SERVER, UNKNOWN, dataAi, kpiLive,
  type A2AResult, type ActivityPeriod, type ActivityReportOutput, type CompanyFigure, type UnknownSection,
} from "@/lib/data";

// 회사 현황 리포트: 데이터 AI(8106) activity_report 출력 중 회사 숫자와 달라진 점을 보여 준다.
// 부서별 AI 업무·부서 간 협업(departments·collaboration)은 AI끼리 일한 기록이라 데이터 분석 탭에서 뺐다.
// 서버는 그대로 계산해 보내고, 화면만 그리지 않는다.
// 숫자는 모두 서버 값이다. 화면은 단위만 바꾼다(ms → 초, 원 콤마, 비율 → %).
// 직전 기간 대비는 화살표(늘었음·줄었음·같음)와 직전 값만 보여 주고, 차이를 따로 계산하지 않는다.
// 서버가 셀 수 없다고 한 칸(unknown_sections)은 그 칸에 "확인할 수 없음 · 이유"로 보여 준다. null은 0으로 그리지 않는다.

const PERIOD_LABEL: Record<ActivityPeriod, string> = { day: "일간", week: "주간", month: "월간" };
const NOTHING_NOTABLE = "특이사항 없음";

/** 한국 시간 어제 날짜 (YYYY-MM-DD). 입력 기본값 */
function yesterdayKst(): string {
  return new Date(Date.now() + 9 * 3600_000 - 86_400_000).toISOString().slice(0, 10);
}

// ------------------------------------------------------------------- 단위 바꾸기
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const plain = (v: number | null | undefined, unit = "") => (isNum(v) ? `${v.toLocaleString("ko-KR")}${unit}` : null);
const percent = (v: number | null | undefined) => (isNum(v) ? `${(v * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%` : null);

/** 이 칸(section)과 그 아래 칸의 "확인할 수 없음" 이유 */
function reasonsFor(unknown: UnknownSection[], ...sections: string[]): string[] {
  return unknown.filter((u) => sections.some((s) => u.section === s || u.section.startsWith(`${s}.`))).map((u) => u.reason);
}

function Unknown({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {reasons.map((r, i) => <li key={i} className="text-[11px] leading-relaxed text-amber-700">{UNKNOWN} · {r}</li>)}
    </ul>
  );
}

/** 직전 기간 대비: 화살표 + 직전 값. 직전 값을 받지 못한 이유(previousReasons)는 "직전" 옆에 작게, 이 칸에만 */
function Trend({ now, before, text, previousReasons = [] }: {
  now: number | null | undefined; before: number | null | undefined; text: string | null; previousReasons?: string[];
}) {
  if (previousReasons.length && !isNum(before)) {
    return <span className="text-[11px] text-amber-700">직전 {UNKNOWN} · {previousReasons.join(" · ")}</span>;
  }
  if (!isNum(now) || !isNum(before) || text == null) return <span className="text-[11px] text-ink-500">직전 비교 {UNKNOWN}</span>;
  const [mark, tone, word] = now > before ? ["▲", "text-emerald-700", "늘었음"] : now < before ? ["▼", "text-red-700", "줄었음"] : ["–", "text-ink-500", "같음"];
  return (
    <span className={`text-[11px] tabular-nums ${tone}`}>
      <span aria-hidden="true">{mark}</span><span className="sr-only">{word},</span> 직전 {text}
    </span>
  );
}

function Value({ text, reasons = [] }: { text: string | null; reasons?: string[] }) {
  return text == null
    ? <><p className="text-lg font-semibold text-ink-500">–</p>{reasons.length ? <Unknown reasons={reasons} /> : <p className="text-[11px] text-amber-700">{UNKNOWN}</p>}</>
    : <><p className="text-lg font-semibold tabular-nums text-ink-900">{text}</p><Unknown reasons={reasons} /></>;
}

// ------------------------------------------------------------------- 회사 현황
function CompanyCell({ label, figure, show, reasons, previousReasons, children }: {
  label: string; figure: CompanyFigure; show: (v: number | null) => string | null; reasons: string[]; previousReasons: string[]; children?: ReactNode;
}) {
  return (
    <div className="rounded-2xl bg-surface-2 px-4 py-3">
      <p className="text-xs font-medium text-ink-700">{label}</p>
      <Value text={show(figure.value)} reasons={reasons} />
      <Trend now={figure.value} before={figure.previous_period} text={show(figure.previous_period)} previousReasons={previousReasons} />
      {children}
    </div>
  );
}

function Company({ o }: { o: ActivityReportOutput }) {
  const c = o.company;
  // 이번 기간 이유는 값 아래, 직전 기간 이유는 "직전" 옆
  const r = (key: string) => ({ reasons: reasonsFor(o.unknown_sections, `company.${key}`), previousReasons: reasonsFor(o.unknown_sections, `company.previous_period.${key}`) });
  const money = (v: number | null) => (isNum(v) ? won(v) : null);
  return (
    <Panel id="data-report-company" title="회사 현황" hint="매출·주문·결제는 재무 AI와 쇼핑몰 기록, 사건 수는 공통 로그에서 받은 값입니다.">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <CompanyCell label="총거래액" figure={c.gmv} show={money} {...r("gmv")}>
          {c.gmv.note && <p className="mt-1 text-[11px] leading-relaxed text-ink-500">{c.gmv.note}</p>}
        </CompanyCell>
        <CompanyCell label="수수료 매출" figure={c.commission_revenue} show={money} {...r("commission_revenue")}>
          {c.commission_revenue.note && <p className="mt-1 text-[11px] leading-relaxed text-ink-500">{c.commission_revenue.note}</p>}
        </CompanyCell>
        <CompanyCell label="주문 수" figure={c.order_count} show={(v) => plain(v, "건")} {...r("order_count")} />
        <CompanyCell label="결제 성공률" figure={c.payment_success_rate} show={percent} {...r("payment_success_rate")} />
        <CompanyCell label="사건 수" figure={c.incident_count} show={(v) => plain(v, "건")} {...r("incident_count")} />
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------- 특이사항·요약
function Highlights({ res }: { res: A2AResult<ActivityReportOutput> }) {
  const o = res.output;
  if (!o.highlights.length && o.summary === NOTHING_NOTABLE) {
    return (
      <section className="card flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
        <p className="text-2xl font-bold text-ink-900">{NOTHING_NOTABLE}</p>
        <p className="max-w-md text-sm text-ink-500">
          {o.period === "day" ? "어제와 비교해" : "직전 기간과 비교해"} 크게 달라진 숫자가 없어요. 위 회사 현황 숫자는 그대로 볼 수 있습니다.
        </p>
      </section>
    );
  }
  return (
    <Panel id="data-report-highlights" title="달라진 점" hint="직전 기간과 비교해 가장 크게 달라진 항목입니다. 고르는 것과 숫자는 서버 코드가 정합니다."
      tools={o.summary_source === "template" ? <Chip label="자동 문장" tone={TONE.muted} /> : undefined}>
      {o.highlights.length > 0 && (
        <ul className="mb-4 grid gap-3 md:grid-cols-3">
          {o.highlights.map((h) => (
            <li key={h.key} className="rounded-2xl bg-surface-2 px-4 py-3">
              <p className="text-xs font-medium text-ink-700">{h.label}</p>
              <p className="mt-1 text-lg font-semibold tabular-nums text-ink-900">
                {h.current.toLocaleString("ko-KR")}
                <span className={`ml-2 text-sm ${h.change_percent > 0 ? "text-emerald-700" : "text-red-700"}`}>
                  {h.change_percent > 0 ? "▲" : "▼"} {Math.abs(h.change_percent).toLocaleString("ko-KR")}%
                </span>
              </p>
              <p className="text-[11px] tabular-nums text-ink-500">직전 {h.previous.toLocaleString("ko-KR")}</p>
            </li>
          ))}
        </ul>
      )}
      <div className="rounded-2xl bg-brand-50 px-4 py-3 text-sm leading-relaxed text-brand-700">
        {o.summary.split("\n").filter(Boolean).map((line, i) => <p key={i}>{line}</p>)}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------- 화면
/** 언제 만든 값인지: 한국 시간 "10월 29일 00시 31분" (오늘이면 날짜 없이 "00시 31분") */
function snapshotTime(at: string | null | undefined): string | null {
  if (!at) return null;
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(when).map((x) => [x.type, x.value]),
  );
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" }) === when.toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
  const time = `${parts.hour}시 ${parts.minute}분`;
  return today ? time : `${parts.month}월 ${parts.day}일 ${time}`;
}

function Report({ res }: { res: A2AResult<ActivityReportOutput> }) {
  const o = res.output;
  const last = new Date(`${o.end}T00:00:00+09:00`);
  last.setDate(last.getDate() - 1);
  const until = last.toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
  return (
    <div className="space-y-5">
      <p className="flex flex-wrap items-center gap-x-2 text-xs text-ink-500">
        <span>{PERIOD_LABEL[o.period]} · {o.start === until ? o.start : `${o.start} ~ ${until}`} (한국 시간)</span>
        {snapshotTime(o.snapshot_at) && <span className="font-medium text-ink-700">{snapshotTime(o.snapshot_at)} 기준</span>}
        {o.snapshot_note && <span>· {o.snapshot_note}</span>}
      </p>
      <Company o={o} />
      <Highlights res={res} />
    </div>
  );
}

export function ActivityReport({ tick }: { tick: number }) {
  const [period, setPeriod] = useState<ActivityPeriod>("day");
  const [start, setStart] = useState<string>(yesterdayKst());
  // 페이지는 30초마다 새로 받지만 주간·월간 리포트는 그보다 오래 걸린다. 받는 중이면 그 차례는 건너뛴다
  // (안 그러면 응답이 오기 전에 새 요청이 나가 화면이 계속 "불러오는 중"이고 재무 호출만 쌓인다)
  const busy = useRef(false);
  const [refresh, setRefresh] = useState(tick);
  useEffect(() => { if (!busy.current) setRefresh(tick); }, [tick]);
  const res = useResource(`activity-${period}-${start}`, () => dataAi.activityReport(period, start || null).then((r) => ({ data: r, meta: { mock: false } })), refresh);
  useEffect(() => { busy.current = res.loading; }, [res.loading]);

  if (!kpiLive) {
    return (
      <Panel id="data-report" title="회사 현황 리포트">
        <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 리포트를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</Empty>
      </Panel>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div role="radiogroup" aria-label="기간" className="flex gap-1 rounded-xl bg-surface p-1 ring-1 ring-ink-100">
          {(Object.keys(PERIOD_LABEL) as ActivityPeriod[]).map((p) => (
            <button key={p} type="button" role="radio" aria-checked={period === p}
              onClick={() => { setPeriod(p); setStart(p === "day" ? yesterdayKst() : ""); }}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${period === p ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
              {PERIOD_LABEL[p]}
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1 text-xs text-ink-700">
          {period === "day" ? "날짜" : period === "week" ? "시작 날짜 (비우면 지난주)" : "그 달의 아무 날짜 (비우면 지난달)"}
          <input type="date" className="field" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        {res.loading && <span className="text-xs text-ink-500" aria-live="polite">불러오는 중…</span>}
      </div>
      {!res.env ? (
        res.error ? <ErrorBox message={`${res.error}. ${DATA_SERVER}가 켜져 있는지 확인해 주세요.`} onRetry={res.reload} /> : <Waiting />
      ) : (
        <>
          {res.error && <ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} />}
          <Report res={res.env.data} />
        </>
      )}
    </div>
  );
}
