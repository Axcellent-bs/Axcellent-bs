import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { humanId, shortDateTime } from "@/lib/format";
import {
  barWidth, checkKo, finance, incidentName, krw, ledgerSourceKo, plainText, purposeKo, sortIncidents,
  type CouponStatus, type IncidentCheck, type IncidentCoupon, type IncidentDetail, type IncidentSummary,
} from "@/lib/finance";
import { Chip, Empty, ErrorBox, MetaLine, MockBadge, RefList, ResourceBody, TONE, Waiting, useResource, type Resource } from "./bits";

// 장애 사건 재무 (시나리오 A). 기준: ax-backend docs/notes/finance/2026-10-23-finance-redesign.md §12·§12-1
// 재무가 하는 일: 사건별 장부 기록과 사건 후 감사. 재무는 장애 대응 흐름의 단계가 아니다(#415).
// 건별 승인은 하지 않는다(안전 장치가 A/C로 판정). 숫자는 서버 값을 그대로 보여 준다.

const CATEGORY_KO: Record<string, string> = { books: "도서", stationery: "문구", ebooks: "전자책", goods: "굿즈" };
const COUPON_STATUS: Record<CouponStatus, { label: string; tone: string }> = {
  executed: { label: "지급됨", tone: TONE.ok },
  held: { label: "보류 중 (사람 결정 기다림)", tone: TONE.warn },
  rejected: { label: "거절됨", tone: TONE.bad },
  failed: { label: "지급 실패", tone: TONE.bad },
};
const count = (n: number | null | undefined, unit: string) => (n == null ? "확인할 수 없음" : `${n.toLocaleString("ko-KR")}${unit}`);

/** ✓ / ✗ / – 표시 */
function Mark({ ok, label }: { ok: boolean | null; label?: string }) {
  const [sym, cls, text] = ok === true ? ["✓", "bg-emerald-100 text-emerald-700", "맞음"] : ok === false ? ["✗", "bg-red-100 text-red-700", "문제 있음"] : ["–", "bg-ink-100 text-ink-500", "확인할 수 없음"];
  return (
    <span className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-sm font-bold ${cls}`} role="img" aria-label={label ?? text} title={label ?? text}>{sym}</span>
  );
}

function checkChip(ok: boolean | null) {
  if (ok === true) return <Chip label="감사 이상 없음" tone={TONE.ok} />;
  if (ok === false) return <Chip label="감사에서 확인할 점" tone={TONE.bad} />;
  return <Chip label="감사 전" tone={TONE.muted} />;
}

// ---------------------------------------------------------------- 사건 고르기 (탭 + 이전 사건 목록)
/** 탭으로 보여 줄 최근 사건 수. 좁은 화면(휴대폰)에서는 줄이고 나머지는 "이전 사건" 목록으로 */
const TABS_WIDE = 5;
const TABS_NARROW = 2;
const NARROW_QUERY = "(max-width: 639px)";

function useNarrow() {
  const get = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(NARROW_QUERY).matches;
  const [narrow, setNarrow] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(NARROW_QUERY);
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

/** "10/25" 같은 짧은 날짜 */
function shortDay(iso: string | null) {
  if (!iso) return "날짜 모름";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 감사 ✗ = 빨강, C 보류 = 주황. 둘 다 아니면 표시 없음 */
function statusDot(i: IncidentSummary): { cls: string; label: string } | null {
  if (i.checks_ok === false) return { cls: "bg-red-500", label: i.held_count > 0 ? `감사에서 확인할 점, 보류 중 보상 ${i.held_count}건` : "감사에서 확인할 점" };
  if (i.held_count > 0) return { cls: "bg-amber-500", label: `보류 중 보상 ${i.held_count}건` };
  return null;
}

const tabId = (contextId: string) => `inc-tab-${contextId}`;
const PANEL_ID = "incident-panel";

function IncidentTabs({ recent, older, selected, onSelect }: { recent: IncidentSummary[]; older: IncidentSummary[]; selected: string | null; onSelect: (id: string) => void }) {
  const olderSelected = older.some((i) => i.context_id === selected);
  const recentSelected = recent.some((i) => i.context_id === selected);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  // ← → Home End 로 탭 사이를 옮긴다(옮기면 바로 그 사건을 연다)
  function onKey(e: KeyboardEvent<HTMLButtonElement>, idx: number) {
    const last = recent.length - 1;
    const to = e.key === "ArrowRight" ? (idx === last ? 0 : idx + 1)
      : e.key === "ArrowLeft" ? (idx === 0 ? last : idx - 1)
        : e.key === "Home" ? 0 : e.key === "End" ? last : null;
    if (to == null) return;
    e.preventDefault();
    refs.current[to]?.focus();
    onSelect(recent[to].context_id);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div role="tablist" aria-label="장애 사건 (최근 먼저)" className="flex min-w-0 flex-wrap gap-1">
        {recent.map((i, idx) => {
          const on = i.context_id === selected;
          const dot = statusDot(i);
          return (
            <button key={i.context_id} ref={(el) => { refs.current[idx] = el; }} type="button" role="tab" id={tabId(i.context_id)}
              aria-selected={on} aria-controls={PANEL_ID} tabIndex={on || (!recentSelected && idx === 0) ? 0 : -1}
              onClick={() => onSelect(i.context_id)} onKeyDown={(e) => onKey(e, idx)}
              className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-600 text-white" : "bg-surface text-ink-700 ring-1 ring-ink-100 hover:bg-ink-100"}`}>
              <span className="truncate">{incidentName(i.context_id, i.component)}</span>
              <span className={`shrink-0 text-xs tabular-nums ${on ? "text-white/80" : "text-ink-500"}`}>{shortDay(i.opened_at)}</span>
              {dot && <span className={`h-2 w-2 shrink-0 rounded-full ${dot.cls} ${on ? "ring-2 ring-white" : ""}`} role="img" aria-label={dot.label} title={dot.label} />}
            </button>
          );
        })}
      </div>
      {older.length > 0 && (
        <label className="min-w-0 max-w-full">
          <span className="sr-only">이전 사건 고르기</span>
          <select value={olderSelected ? selected ?? "" : ""} onChange={(e) => { if (e.target.value) onSelect(e.target.value); }}
            className={`max-w-full rounded-full border-0 py-2 pl-4 pr-8 text-sm font-medium ring-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${olderSelected ? "bg-brand-600 text-white ring-brand-600" : "bg-surface text-ink-700 ring-ink-100"}`}>
            <option value="">이전 사건 {older.length}건</option>
            {older.map((i) => {
              const dot = statusDot(i);
              return (
                <option key={i.context_id} value={i.context_id}>
                  {incidentName(i.context_id, i.component)} · {shortDay(i.opened_at)}{dot ? ` · ${dot.label}` : ""}
                </option>
              );
            })}
          </select>
        </label>
      )}
    </div>
  );
}

/** 상세 맨 위 요약: 피해 + 보상, 감사 상태 (사건 목록 값 그대로) */
function SummaryStrip({ s }: { s: IncidentSummary }) {
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-2 rounded-2xl bg-surface-2 px-4 py-3">
      <p className="text-sm tabular-nums text-ink-700">피해 {krw(s.damage_amount)} · 보상으로 나간 돈 {krw(s.compensation_total)}</p>
      <div className="flex flex-wrap gap-1.5">
        {checkChip(s.checks_ok)}
        {s.held_count > 0 && <Chip label={`보류 중 보상 ${s.held_count}건`} tone={TONE.warn} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 장부 출처
function LedgerLine({ d }: { d: IncidentDetail }) {
  const src = ledgerSourceKo(d.ledger_source);
  return (
    <p className="rounded-2xl bg-surface-2 px-4 py-3 text-xs text-ink-700">
      <b>장부 출처: {src.label}</b>{src.detail && <span className="text-ink-500"> · {src.detail}</span>}
    </p>
  );
}

// ---------------------------------------------------------------- 카드 3장
function Card({ title, value, tone = "text-ink-900", children }: { title: string; value: string; tone?: string; children?: ReactNode }) {
  const unknown = value === "확인할 수 없음";
  return (
    <div className="rounded-2xl border border-ink-100 bg-surface px-4 py-3">
      <p className="text-xs font-medium text-ink-500">{title}</p>
      <p className={`mt-0.5 break-words font-bold tabular-nums ${unknown ? "text-sm text-ink-500" : `text-2xl ${tone}`}`}>{value}</p>
      {children && <div className="mt-2 space-y-1 text-xs text-ink-700">{children}</div>}
    </div>
  );
}

function Bar({ ratio, label }: { ratio: number | null; label: string }) {
  const w = barWidth(ratio);
  const color = ratio == null ? "bg-ink-300" : ratio >= 1 ? "bg-red-500" : ratio >= 0.8 ? "bg-amber-400" : "bg-emerald-500";
  return (
    <div className="h-2 overflow-hidden rounded-full bg-ink-100" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(w)}>
      <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(w, ratio ? 2 : 0)}%` }} />
    </div>
  );
}

function Cards({ d }: { d: IncidentDetail }) {
  const { impact, compensation: c, limits } = d;
  const pc = limits.per_customer, pi = limits.per_incident;
  const piRatio = pi.limit ? pi.used / pi.limit : null;
  const pcRatio = pc.limit ? pc.max_used / pc.limit : null;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      <Card title="피해 (데이터 AI 분석 그대로)" value={krw(impact?.damage_amount)}>
        {impact ? (
          <>
            <p>실패한 주문 <b className="tabular-nums">{count(impact.failed_orders, "건")}</b> · 피해 고객 <b className="tabular-nums">{count(impact.affected_customers, "명")}</b></p>
            {impact.by_category.length > 0 && (
              <ul className="space-y-0.5 border-t border-ink-100 pt-1.5">
                {impact.by_category.map((k) => (
                  <li key={k.category} className="flex justify-between gap-2 tabular-nums">
                    <span>{CATEGORY_KO[k.category] ?? "기타"} {k.orders}건</span><span>{krw(k.amount)}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[11px] text-ink-500">재무는 이 숫자를 다시 계산하지 않습니다.{impact.collected_at && ` ${shortDateTime(impact.collected_at)} 분석`}</p>
          </>
        ) : <p className="text-ink-500">데이터 AI의 영향 분석을 아직 받지 못했습니다.</p>}
      </Card>

      <Card title="보상으로 나간 돈 (장부)" value={krw(c.total_executed)}>
        <p>이 사건으로 실제 나간 금액입니다.</p>
        <p className={c.count_held ? "font-medium text-amber-700" : ""}>
          보류 중 <b className="tabular-nums">{krw(c.total_held)}</b>{c.count_held > 0 && " · 사람이 보류 큐에서 결정해야 합니다"}
        </p>
        <p className="text-[11px] text-ink-500">보상 판정은 고객지원, 지급은 마케팅이 합니다. 재무는 빠져나간 돈만 적습니다. 보류 중인 금액은 아직 나간 돈이 아닙니다.</p>
      </Card>

      <Card title="보상 한도 사용" value={pi.limit == null ? "확인할 수 없음" : `${Math.round((pi.used / pi.limit) * 1000) / 10}%`}>
        <div className="space-y-1">
          <p className="flex justify-between gap-2"><span>사건 전체</span><span className="tabular-nums">{krw(pi.used)} / {krw(pi.limit)}</span></p>
          <Bar ratio={piRatio} label="사건당 보상 한도 사용률" />
          <p className="text-[11px] text-ink-500">남은 한도 {krw(pi.remaining)}</p>
        </div>
        <div className="space-y-1 border-t border-ink-100 pt-1.5">
          <p className="flex justify-between gap-2"><span>고객 한 명 최대</span><span className="tabular-nums">{krw(pc.max_used)} / {krw(pc.limit)}</span></p>
          <Bar ratio={pcRatio} label="고객당 보상 한도 사용률" />
          {pc.buyer_id && <p className="text-[11px] text-ink-500">가장 많이 받은 고객: {humanId(pc.buyer_id)}</p>}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- 보상 받은 고객
function Recipients({ d, onGoToQueue }: { d: IncidentDetail; onGoToQueue: (reviewId: string | null) => void }) {
  const rows = [...d.compensation.coupons].sort((a, b) => a.at.localeCompare(b.at));
  const needsHuman = (r: IncidentCoupon) => r.grade === "C" || r.status === "held";
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink-900">보상으로 나간 내역 (장부) <span className="font-normal text-ink-500">{rows.length}건</span></h3>
      <p className="mt-0.5 text-xs text-ink-500">
        누구에게 줄지는 고객지원 AI가 정하고 지급은 마케팅 AI가 합니다. 재무는 빠져나간 돈을 적고, 감사 때 근거로 씁니다.
        A는 한도 안이라 안전 장치가 바로 지급한 쿠폰, C는 한도를 넘거나 금액이 맞지 않아 사람 결정을 기다리는 쿠폰입니다(10분 안에 아무도 정하지 않으면 자동 취소).
      </p>
      {rows.length ? (
        <div className="mt-2 overflow-x-auto rounded-2xl border border-ink-100">
          <table className="w-full min-w-[680px] text-sm">
            <thead className="bg-surface-2 text-left text-xs text-ink-500">
              <tr>
                <th className="px-3 py-2 font-medium">고객</th>
                <th className="px-3 py-2 text-right font-medium">금액</th>
                <th className="px-3 py-2 font-medium">판정</th>
                <th className="px-3 py-2 font-medium">상태</th>
                <th className="px-3 py-2 font-medium">영향 고객?</th>
                <th className="px-3 py-2 font-medium">기록 번호</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const st = COUPON_STATUS[r.status] ?? { label: r.status, tone: TONE.muted };
                return (
                  <tr key={r.execution_id} className={`border-t border-ink-100 align-top ${needsHuman(r) ? "bg-amber-50/60" : ""}`}>
                    <td className="px-3 py-2">
                      <b className="text-ink-900">{humanId(r.buyer_id)}</b>
                      <p className="text-[11px] text-ink-500">{purposeKo(r.reason_code, r.purpose_code)} · {shortDateTime(r.at)}</p>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {krw(r.amount)}
                      {r.count > 1 && <p className="text-[11px] text-ink-500">{krw(r.unit_value)} × {r.count}장</p>}
                    </td>
                    <td className="px-3 py-2">
                      <Chip label={r.grade === "A" ? "A 바로 지급" : "C 사람 확인"} tone={r.grade === "A" ? TONE.ok : TONE.warn} />
                    </td>
                    <td className="px-3 py-2">
                      <Chip label={st.label} tone={st.tone} />
                      {needsHuman(r) && (
                        <button type="button" onClick={() => onGoToQueue(r.review_id)} className="mt-1 block text-xs font-medium text-brand-700 hover:underline">보류 큐에서 결정 →</button>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <span className="inline-flex items-center gap-1.5">
                        <Mark ok={r.in_impact} label={r.in_impact === true ? "영향 고객 맞음" : r.in_impact === false ? "영향 고객 목록에 없음" : "확인 불가"} />
                        <span className="text-xs text-ink-700">{r.in_impact === true ? "맞음" : r.in_impact === false ? "목록에 없음" : "확인 불가"}</span>
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <RefList refs={[r.coupon_id, r.execution_id, r.review_id]} label="보기" />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <div className="mt-2"><Empty>이 사건으로 나간 보상 쿠폰이 아직 없습니다. 고객지원 AI가 요청하면 마케팅 AI가 쿠폰을 주고, 재무가 장부에 적습니다.</Empty></div>}
    </div>
  );
}

// ---------------------------------------------------------------- 사건 후 감사
function Checks({ checks }: { checks: IncidentCheck[] }) {
  const bad = checks.filter((c) => c.ok === false).length;
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink-900">
        사건 후 감사 <span className={`font-normal ${bad ? "text-red-700" : "text-ink-500"}`}>{checks.length ? (bad ? `확인할 점 ${bad}개` : "") : ""}</span>
      </h3>
      <p className="mt-0.5 text-xs text-ink-500">사건이 끝난 뒤 보상이 규칙대로 나갔는지 다시 봅니다. 한 장씩 막는 것이 아니라 나중에 점검하는 것입니다.</p>
      {checks.length ? (
        <ul className="mt-2 space-y-2">
          {checks.map((c) => (
            <li key={c.code} className={`flex gap-3 rounded-2xl border px-4 py-3 text-sm ${c.ok === false ? "border-red-100 bg-red-50/60" : "border-ink-100"}`}>
              <Mark ok={c.ok} />
              <div className="min-w-0">
                <b className="text-ink-900">{checkKo(c.code, c.title)}</b>
                {c.detail && <p className="mt-0.5 text-ink-700">{plainText(c.detail)}</p>}
                <RefList refs={c.refs} />
              </div>
            </li>
          ))}
        </ul>
      ) : <div className="mt-2"><Empty>아직 감사하지 않았습니다. 사건이 끝나면 점검 결과가 여기에 나옵니다.</Empty></div>}
    </div>
  );
}

function NotOurJob() {
  return (
    <aside className="rounded-2xl border border-dashed border-ink-300 px-4 py-3 text-xs leading-relaxed text-ink-700">
      <p className="font-semibold text-ink-900">재무가 하지 않는 일</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4">
        <li><b>쿠폰 한 장씩 승인</b>: 안전 장치가 판정합니다. 한도 안이면 A로 바로 지급, 넘거나 금액이 맞지 않으면 C로 보류합니다.</li>
        <li><b>누구에게 얼마를 줄지 정하기</b>: 고객지원 AI가 보상 기준표로 정합니다.</li>
        <li><b>쿠폰 지급</b>: 마케팅 AI가 합니다. 쿠폰 발행·관리는 재무 업무가 아닙니다 (마케팅·고객지원 합의).</li>
        <li><b>장애 손실 집계</b>: 재무 일이 아닙니다. 장애 비용은 평상시 장부로 들어가 결산·감사에서 드러납니다.</li>
      </ul>
      <p className="mt-1 text-ink-500">재무는 보상·환불로 <b>빠져나간 돈</b>을 장부에 적고, 사건이 끝나면 감사합니다.</p>
    </aside>
  );
}

// ---------------------------------------------------------------- 사건 한 건
function IncidentDetailView({ id, summary, tick, onGoToQueue }: { id: string; summary: IncidentSummary | null; tick: number; onGoToQueue: (reviewId: string | null) => void }) {
  const res = useResource(`incident:${id}`, () => finance.incident(id), tick);
  const meta = res.env?.meta;
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold text-ink-900">{incidentName(id, res.env?.data.component ?? summary?.component)}</h3>
        <MockBadge meta={meta} />
        {res.loading && res.env && <span className="text-[11px] text-ink-500" aria-live="polite">새로 받는 중…</span>}
      </div>
      {res.env?.data.window && (
        <p className="-mt-3 text-xs text-ink-500">장애 시간 {shortDateTime(res.env.data.window.from)} ~ {shortDateTime(res.env.data.window.to)}</p>
      )}
      {summary && <SummaryStrip s={summary} />}
      <MetaLine meta={meta} />
      <ResourceBody res={res}>
        {(d) => (
          <div className="space-y-5">
            <LedgerLine d={d} />
            <Cards d={d} />
            <Recipients d={d} onGoToQueue={onGoToQueue} />
            <Checks checks={d.checks} />
            <NotOurJob />
          </div>
        )}
      </ResourceBody>
    </div>
  );
}

// ---------------------------------------------------------------- 장애 사건 재무 (기본 화면)
export function IncidentFinance({ list, selected, onSelect, tick, onGoToQueue }: {
  list: Resource<IncidentSummary[]>;
  selected: string | null;
  onSelect: (id: string) => void;
  tick: number;
  onGoToQueue: (reviewId: string | null) => void;
}) {
  const items = sortIncidents(list.env?.data ?? []);
  const narrow = useNarrow();
  const tabCount = narrow ? TABS_NARROW : TABS_WIDE;
  const recent = items.slice(0, tabCount);
  const older = items.slice(tabCount);
  const selectedSummary = items.find((i) => i.context_id === selected) ?? null;
  const panelLabel = selected && recent.some((i) => i.context_id === selected)
    ? { "aria-labelledby": tabId(selected) }
    : { "aria-label": selected ? incidentName(selected, selectedSummary?.component) : "장애 사건" };
  const src = list.env?.meta.ledger_source ? ledgerSourceKo(list.env.meta.ledger_source) : null;
  return (
    <section id="incident-finance" aria-labelledby="incident-finance-title" className="card scroll-mt-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <h2 id="incident-finance-title" className="flex flex-wrap items-center gap-2 text-base font-semibold text-ink-900">
            장애 사건 재무 <MockBadge meta={list.env?.meta} />
          </h2>
          <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-ink-500">
            결제 같은 서비스에 장애가 나면 총괄 AI가 사건을 열고, 보상은 고객지원 AI가 정해 마케팅 AI가 지급합니다.
            재무는 보상·환불로 <b>빠져나간 돈</b>을 사건별 장부에 적고, 사건이 끝나면 규칙대로 나갔는지 감사합니다. 쿠폰 업무와 장애 손실 집계는 재무가 하지 않습니다.
          </p>
          {src && <p className="text-[11px] text-ink-500">장부 출처: {src.label}</p>}
        </div>
      </div>

      <div className="mt-4">
        {!list.env ? (
          list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : <Waiting what="장애 사건을 불러오는 중…" />
        ) : (
          <>
            {list.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 목록을 보여 줍니다. ${list.error}`} onRetry={list.reload} /></div>}
            {items.length === 0 ? (
              <Empty>
                아직 재무가 아는 장애 사건이 없습니다. 장애가 나서 총괄 AI가 사건을 열고 보상 쿠폰이 나가면 여기에 나타납니다.
              </Empty>
            ) : (
              <div className="space-y-4">
                <IncidentTabs recent={recent} older={older} selected={selected} onSelect={onSelect} />
                {selected ? (
                  <div role="tabpanel" id={PANEL_ID} tabIndex={0} {...panelLabel}
                    className="rounded-2xl border border-ink-100 p-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 sm:p-5">
                    <IncidentDetailView key={selected} id={selected} summary={selectedSummary} tick={tick} onGoToQueue={onGoToQueue} />
                  </div>
                ) : <Empty>위에서 사건을 고르세요.</Empty>}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
