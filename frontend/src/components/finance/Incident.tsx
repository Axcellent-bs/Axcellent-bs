import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { humanId, shortDateTime } from "@/lib/format";
import {
  CATEGORY_KO, finance, incidentName, krw, ledgerSourceKo, purposeKo, sortIncidents,
  type IncidentDetail, type IncidentSummary,
} from "@/lib/finance";
import { Empty, ErrorBox, MetaLine, MockBadge, RefList, ResourceBody, Waiting, useResource, type Resource } from "./bits";

// 장애 이벤트 재무 (시나리오 A). 기준: ax-backend docs/notes/finance/2026-10-23-finance-redesign.md §12·§12-1
// 재무가 하는 일: 끝난 쿠폰·환불 금액을 이벤트별 장부에 적기.
// 숫자는 서버 값을 그대로 보여 준다.

const count = (n: number | null | undefined, unit: string) => (n == null ? "확인할 수 없음" : `${n.toLocaleString("ko-KR")}${unit}`);

// ---------------------------------------------------------------- 이벤트 고르기 (탭 + 이전 이벤트 목록)
/** 탭으로 보여 줄 최근 이벤트 수. 좁은 화면(휴대폰)에서는 줄이고 나머지는 "이전 이벤트" 목록으로 */
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

const tabId = (contextId: string) => `inc-tab-${contextId}`;
const PANEL_ID = "incident-panel";

function IncidentTabs({ recent, older, selected, onSelect }: { recent: IncidentSummary[]; older: IncidentSummary[]; selected: string | null; onSelect: (id: string) => void }) {
  const olderSelected = older.some((i) => i.context_id === selected);
  const recentSelected = recent.some((i) => i.context_id === selected);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  // ← → Home End 로 탭 사이를 옮긴다(옮기면 바로 그 이벤트를 연다)
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
      <div role="tablist" aria-label="장애 이벤트 (최근 먼저)" className="flex min-w-0 flex-wrap gap-1">
        {recent.map((i, idx) => {
          const on = i.context_id === selected;
          return (
            <button key={i.context_id} ref={(el) => { refs.current[idx] = el; }} type="button" role="tab" id={tabId(i.context_id)}
              aria-selected={on} aria-controls={PANEL_ID} tabIndex={on || (!recentSelected && idx === 0) ? 0 : -1}
              onClick={() => onSelect(i.context_id)} onKeyDown={(e) => onKey(e, idx)}
              className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-600 text-white" : "bg-surface text-ink-700 ring-1 ring-ink-100 hover:bg-ink-100"}`}>
              <span className="truncate" title={i.context_id}>{incidentName(i.context_id, i.component, i.opened_at)}</span>
              <span className={`shrink-0 text-sm tabular-nums ${on ? "text-white/80" : "text-ink-500"}`}>{shortDay(i.opened_at)}</span>
            </button>
          );
        })}
      </div>
      {older.length > 0 && (
        <label className="min-w-0 max-w-full">
          <span className="sr-only">이전 이벤트 고르기</span>
          <select value={olderSelected ? selected ?? "" : ""} onChange={(e) => { if (e.target.value) onSelect(e.target.value); }}
            className={`max-w-full rounded-full border-0 py-2 pl-4 pr-8 text-sm font-medium ring-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${olderSelected ? "bg-brand-600 text-white ring-brand-600" : "bg-surface text-ink-700 ring-ink-100"}`}>
            <option value="">이전 이벤트 {older.length}건</option>
            {older.map((i) => (
              <option key={i.context_id} value={i.context_id}>
                {incidentName(i.context_id, i.component)} · {shortDay(i.opened_at)}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}

/** 상세 맨 위 요약: 피해 + 보상 (이벤트 목록 값 그대로) */
function SummaryStrip({ s }: { s: IncidentSummary }) {
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-2 rounded-2xl bg-surface-2 px-4 py-3">
      <p className="text-sm tabular-nums text-ink-700">피해 {krw(s.damage_amount)} · 보상으로 나간 돈 {krw(s.compensation_total)}</p>
    </div>
  );
}

// ---------------------------------------------------------------- 장부 출처
function LedgerLine({ d }: { d: IncidentDetail }) {
  const src = ledgerSourceKo(d.ledger_source);
  return (
    <p className="rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-700">
      <b>장부 출처: {src.label}</b>{src.detail && <span className="text-ink-500"> · {src.detail}</span>}
    </p>
  );
}

// ---------------------------------------------------------------- 카드 3장
function Card({ title, value, tone = "text-ink-900", children }: { title: string; value: string; tone?: string; children?: ReactNode }) {
  const unknown = value === "확인할 수 없음";
  return (
    <div className="rounded-2xl border border-ink-100 bg-surface px-4 py-3">
      <p className="text-sm font-medium text-ink-500">{title}</p>
      <p className={`mt-1 break-words font-semibold tabular-nums ${unknown ? "text-sm text-ink-500" : `text-2xl ${tone}`}`}>{value}</p>
      {children && <div className="mt-2 space-y-1 text-sm text-ink-700">{children}</div>}
    </div>
  );
}

function Cards({ d }: { d: IncidentDetail }) {
  const { impact, compensation: c, refunds } = d;
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
            <p className="text-sm text-ink-500">재무는 이 숫자를 다시 계산하지 않습니다.{impact.collected_at && ` ${shortDateTime(impact.collected_at)} 분석`}</p>
          </>
        ) : <p className="text-ink-500">{d.window ? "데이터 AI의 영향 분석을 아직 받지 못했습니다." : "장애 구간을 몰라 계산하지 못했습니다. 총괄 AI의 이벤트 기록은 서버를 다시 켜면 비워집니다."}</p>}
      </Card>

      <Card title="보상으로 나간 돈 (장부)" value={krw(c.total_executed)}>
        <p>이 이벤트로 실제 나간 금액입니다.</p>
      </Card>

      <Card title="환불로 나간 돈 (장부)" value={krw(refunds?.total)}>
        {refunds
          ? <p>이 이벤트 구간에 영향 고객에게 나간 환불 <b className="tabular-nums">{count(refunds.count, "건")}</b>입니다.</p>
          : <p className="text-ink-500">장애 구간이나 영향 고객을 몰라 이 이벤트에 묶지 못했습니다.</p>}
        <p>보상과 환불을 더한 돈 <b className="tabular-nums">{krw(d.money_out_total)}</b></p>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- 보상 받은 고객
function Recipients({ d }: { d: IncidentDetail }) {
  const rows = [...d.compensation.coupons].sort((a, b) => a.at.localeCompare(b.at));
  const noCustomer = rows.filter((r) => !r.buyer_id).length;
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink-900">보상으로 나간 내역 (장부) <span className="font-normal text-ink-500">{rows.length}건</span></h3>
      {rows.length ? (
        <div className="mt-2 overflow-x-auto rounded-2xl border border-ink-100">
          {/* 열 너비를 고정한다. 기록 번호를 펼쳐도 금액 열이 움직이지 않게 */}
          <table className="w-full min-w-[480px] table-fixed text-sm">
            <thead className="bg-surface-2 text-left text-sm text-ink-500">
              <tr>
                <th className="px-3 py-2 font-medium">고객</th>
                <th className="w-36 px-3 py-2 text-right font-medium">금액</th>
                <th className="w-56 px-3 py-2 font-medium">기록 번호</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, n) => {
                return (
                  <tr key={r.execution_id ?? `${r.coupon_id ?? "coupon"}-${n}`} className="border-t border-ink-100 align-top">
                    <td className="px-3 py-2">
                      {/* 쿠폰 이벤트로만 적힌 줄은 고객·실행 번호가 비어 있다. 비어 있어도 화면이 깨지면 안 된다 */}
                      <b className="text-ink-900">{r.buyer_id ? humanId(r.buyer_id) : "고객 정보 없음"}</b>
                      <p className="text-sm text-ink-500">{purposeKo(r.reason_code, r.purpose_code)} · {shortDateTime(r.at)}</p>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {krw(r.amount)}
                      {r.count > 1 && <p className="text-sm text-ink-500">{krw(r.unit_value)} × {r.count}장</p>}
                    </td>
                    <td className="px-3 py-2">
                      <RefList refs={[r.coupon_id, r.execution_id]} label="보기" />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <div className="mt-2"><Empty>이 이벤트로 나간 보상 쿠폰이 아직 없습니다.</Empty></div>}
      {noCustomer > 0 && (
        <p className="mt-2 text-sm leading-relaxed text-ink-500">
          "고객 정보 없음" {noCustomer}건: 안전 장치와 쇼핑몰 기록에서 고객 번호를 찾지 못한 줄입니다. 금액은 지급 기록 그대로입니다.
        </p>
      )}
    </div>
  );
}

/** 처음 보는 사람이 이 화면에서 무엇을 해야 하는지 먼저 알려 준다 */
function WhatToDo() {
  const link = "font-medium text-brand-700 underline hover:text-brand-600";
  return (
    <aside className="rounded-2xl bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink-700">
      <p><b className="text-ink-900">여기서 할 일은 없습니다.</b> 이 장애로 나간 돈을 확인하는 화면이고, 보상과 환불은 재무가 정하지 않습니다.</p>
      <p className="mt-1">이상한 점이 있으면 담당 화면에서 처리합니다.</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
        <li>보상을 못 받은 고객, 환불 문제: <Link to="/support" className={link}>고객지원 AI</Link></li>
        <li>승인을 기다리는 쿠폰: <Link to="/marketing" className={link}>마케팅 AI</Link></li>
        <li>장애 진행 상황: <Link to="/orchestrator" className={link}>총괄</Link></li>
      </ul>
    </aside>
  );
}

// ---------------------------------------------------------------- 이벤트 한 건
function IncidentDetailView({ id, summary, tick }: { id: string; summary: IncidentSummary | null; tick: number }) {
  const res = useResource(`incident:${id}`, () => finance.incident(id), tick);
  const meta = res.env?.meta;
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold text-ink-900">{incidentName(id, res.env?.data.component ?? summary?.component)}</h3>
        <MockBadge meta={meta} />
        {res.loading && res.env && <span className="text-sm text-ink-500" aria-live="polite">새로 받는 중…</span>}
      </div>
      {res.env?.data.window && (
        <p className="-mt-3 text-sm text-ink-500">
          영향 분석 구간 {shortDateTime(res.env.data.window.from)} ~ {shortDateTime(res.env.data.window.to)}{res.env.data.recovered_at && ` · 정상 복귀 ${shortDateTime(res.env.data.recovered_at)}`}
          {meta?.collected_at && <span className="text-xs tabular-nums"> · {shortDateTime(meta.collected_at)} 기준</span>}
        </p>
      )}
      {/* 상세를 받기 전에만 목록 값으로 요약을 보인다. 받은 뒤에는 아래 카드가 같은 숫자를 크게 보여 준다 */}
      {summary && !res.env && <SummaryStrip s={summary} />}
      {!res.env?.data.window && <MetaLine meta={meta} />}
      <ResourceBody res={res}>
        {(d) => (
          <div className="space-y-5">
            {/* 숫자(피해·보상·환불)를 먼저, 안내와 장부 출처는 뒤로 */}
            <Cards d={d} />
            <Recipients d={d} />
            <WhatToDo />
            <LedgerLine d={d} />
          </div>
        )}
      </ResourceBody>
    </div>
  );
}

// ---------------------------------------------------------------- 장애 이벤트 재무 (기본 화면)
export function IncidentFinance({ list, selected, onSelect, tick }: {
  list: Resource<IncidentSummary[]>;
  selected: string | null;
  onSelect: (id: string) => void;
  tick: number;
}) {
  const items = sortIncidents(list.env?.data ?? []);
  const narrow = useNarrow();
  const tabCount = narrow ? TABS_NARROW : TABS_WIDE;
  const recent = items.slice(0, tabCount);
  const older = items.slice(tabCount);
  const selectedSummary = items.find((i) => i.context_id === selected) ?? null;
  const panelLabel = selected && recent.some((i) => i.context_id === selected)
    ? { "aria-labelledby": tabId(selected) }
    : { "aria-label": selected ? incidentName(selected, selectedSummary?.component) : "장애 이벤트" };
  const src = list.env?.meta.ledger_source ? ledgerSourceKo(list.env.meta.ledger_source) : null;
  return (
    <section id="incident-finance" aria-labelledby="incident-finance-title" className="card scroll-mt-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <h2 id="incident-finance-title" className="flex flex-wrap items-center gap-2 text-base font-semibold text-ink-900">
            장애 이벤트 재무 <MockBadge meta={list.env?.meta} />
          </h2>
          <p className="mt-0.5 text-sm leading-relaxed text-ink-500">
            결제 같은 서비스에 장애가 나면 총괄 AI가 이벤트를 열고, 보상은 고객지원 AI가 정해 마케팅 AI가 지급합니다.
            재무는 쿠폰·환불의 끝난 금액을 이벤트별 장부에 적기만 합니다.
          </p>
          {src && <p className="text-sm text-ink-500">장부 출처: {src.label}</p>}
        </div>
      </div>

      <div className="mt-4">
        {!list.env ? (
          list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : <Waiting what="장애 이벤트를 불러오는 중…" />
        ) : (
          <>
            {list.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 목록을 보여 줍니다. ${list.error}`} onRetry={list.reload} /></div>}
            {items.length === 0 ? (
              <Empty>
                아직 재무가 아는 장애 이벤트가 없습니다. 장애가 나서 총괄 AI가 이벤트를 열고 보상 쿠폰이 나가면 여기에 나타납니다.
              </Empty>
            ) : (
              <div className="space-y-4">
                <IncidentTabs recent={recent} older={older} selected={selected} onSelect={onSelect} />
                {selected ? (
                  <div role="tabpanel" id={PANEL_ID} tabIndex={0} {...panelLabel}
                    className="rounded-2xl border border-ink-100 p-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 sm:p-5">
                    <IncidentDetailView key={selected} id={selected} summary={selectedSummary} tick={tick} />
                  </div>
                ) : <Empty>위에서 이벤트를 고르세요.</Empty>}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
