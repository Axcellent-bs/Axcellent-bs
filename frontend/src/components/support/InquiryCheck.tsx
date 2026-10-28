import { useEffect, useState, type ReactNode } from "react";
import { Stat } from "@/components/admin/bits";
import { TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, RefList, TONE, Waiting, useResource } from "@/components/finance/bits";
import { api } from "@/lib/api";
import { krw } from "@/lib/finance";
import { humanId, shortDateTime } from "@/lib/format";
import {
  CAUSE_KO, COMP_REASON_KO, FAILURE_KO, METHOD_KO, NEXT_ACTION_KO, PAY_STATUS_KO, SITUATION_KO, UNKNOWN, ko, support,
  type CompensationSuggestion, type PaymentDiagnosis,
} from "@/lib/support";

// 문의 진단: 고른 문의의 주문 사실로 보상 쿠폰 후보와 결제가 막힌 곳을 본다. 둘 다 읽기 전용이다.
// 보상 요청은 사람이 승인해 따로 보낸다. 이 화면은 후보만 보여 준다.

function Compensation({ s }: { s: CompensationSuggestion }) {
  const f = s.facts;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Chip label={s.eligible ? "보상 대상" : "보상 대상 아님"} tone={s.eligible ? TONE.ok : TONE.muted} />
        {s.reason_code && <Chip label={ko(COMP_REASON_KO, s.reason_code)} tone={TONE.info} />}
      </div>
      <p className="rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-900">{s.message}</p>
      {s.eligible && (
        <div className="flex flex-wrap gap-3">
          <Stat label="쿠폰 한 장" value={krw(s.unit_value)} />
          <Stat label="장수" value={`${s.count}장`} />
          <Stat label="보상 합계" value={krw(s.total_value)} tone="text-accent" />
        </div>
      )}
      {f && (
        <TableView columns={["항목", "값"]} rows={[
          ["주문", f.order_id ? humanId(f.order_id) : UNKNOWN],
          ["약속한 배송일", shortDateTime(f.promised_delivery_at)],
          ["출고", shortDateTime(f.shipped_at)],
          ["도착", shortDateTime(f.delivered_at)],
          ["늦은 날수", f.days_late == null ? UNKNOWN : `${f.days_late}일`],
        ]} />
      )}
      {s.notes.map((n, i) => <p key={i} className="rounded-md bg-ink-100 px-2 py-1 text-[11px] text-ink-500">{n}</p>)}
    </div>
  );
}

function Payment({ d }: { d: PaymentDiagnosis }) {
  const f = d.facts;
  const bad = d.situation !== "paid";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Chip label={ko(SITUATION_KO, d.situation)} tone={bad ? TONE.bad : TONE.ok} />
        <Chip label={`다음: ${ko(NEXT_ACTION_KO, d.next_action)}`} tone={TONE.info} />
        {d.likely_cause && <Chip label={`원인: ${ko(CAUSE_KO, d.likely_cause)}`} tone={TONE.warn} />}
      </div>
      <p className="rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-900">{d.message}</p>
      {d.duplicate_amount > 0 && <Stat label="중복으로 빠진 금액" value={krw(d.duplicate_amount)} tone="text-red-600" />}
      {f && (
        <>
          <div className="flex flex-wrap gap-3">
            <Stat label="주문 금액" value={krw(f.total_amount)} />
            <Stat label="성공한 결제" value={`${f.succeeded_count}건`} />
            <Stat label="실패한 결제" value={`${f.failed_count}건`} tone={f.failed_count ? "text-red-600" : ""} />
          </div>
          <TableView columns={["결제", "금액", "방법", "결과", "실패 사유", "시각"]} rows={f.attempts.map((a) => [
            humanId(a.payment_id), krw(a.amount), ko(METHOD_KO, a.method_code), ko(PAY_STATUS_KO, a.status),
            a.failure_code ? ko(FAILURE_KO, a.failure_code) : "–", shortDateTime(a.created_at),
          ])} />
          <RefList refs={[f.order_id, ...f.attempts.map((a) => a.payment_id)]} />
        </>
      )}
      {d.notes.map((n, i) => <p key={i} className="rounded-md bg-ink-100 px-2 py-1 text-[11px] text-ink-500">{n}</p>)}
    </div>
  );
}

function Result<T>({ id, title, hint, res, children }: {
  id: string; title: string; hint: string; res: ReturnType<typeof useResource<T>>; children: (d: T) => ReactNode;
}) {
  return (
    <Panel id={id} title={title} hint={hint} meta={res.env?.meta}>
      {!res.env ? (res.error ? <ErrorBox message={res.error} onRetry={res.reload} /> : <Waiting />) : (
        <>
          {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
          {children(res.env.data)}
        </>
      )}
    </Panel>
  );
}

export function InquiryCheck({ tick, selected, onSelect }: { tick: number; selected: number | null; onSelect: (id: number) => void }) {
  // 문의 목록은 상담 콘솔과 같은 API. 못 받아도 번호를 직접 넣어 볼 수 있다
  const convs = useResource("support-conversations", async () => ({ data: await api.admin.list(), meta: { mock: false } }), tick);
  const list = convs.env?.data ?? [];
  const current = selected ?? list[0]?.id ?? null;
  const [draft, setDraft] = useState("");
  useEffect(() => { setDraft(current == null ? "" : String(current)); }, [current]);

  const comp = useResource(`comp-${current ?? ""}`, () => {
    if (current == null) throw new Error("고른 문의가 없습니다");
    return support.compensation(current);
  }, tick);
  const pay = useResource(`pay-${current ?? ""}`, () => {
    if (current == null) throw new Error("고른 문의가 없습니다");
    return support.paymentDiagnosis(current);
  }, tick);

  const open = () => {
    const n = Number(draft);
    if (Number.isInteger(n) && n > 0) onSelect(n);
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-0 max-w-full">
          <span className="mb-1 block text-xs font-medium text-ink-700">문의 고르기</span>
          <select value={current != null && list.some((c) => c.id === current) ? current : ""} onChange={(e) => e.target.value && onSelect(Number(e.target.value))}
            className="max-w-full rounded-full border-0 bg-surface py-2 pl-4 pr-8 text-sm ring-1 ring-ink-100">
            <option value="">{convs.env ? "문의를 고르세요" : "문의 목록 불러오는 중…"}</option>
            {list.map((c) => <option key={c.id} value={c.id}>#{c.id} · {c.customer_name} · {c.subject}</option>)}
          </select>
        </label>
        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); open(); }}>
          <label>
            <span className="mb-1 block text-xs font-medium text-ink-700">문의 번호로 열기</span>
            <input value={draft} onChange={(e) => setDraft(e.target.value)} inputMode="numeric" className="w-28 rounded-full border-0 bg-surface px-4 py-2 text-sm ring-1 ring-ink-100" />
          </label>
          <button className="btn-ghost" type="submit">열기</button>
        </form>
      </div>
      {convs.error && !convs.env && <ErrorBox message={`문의 목록을 받지 못했습니다. 번호로 직접 열 수 있습니다. ${convs.error}`} onRetry={convs.reload} />}
      {current == null ? <Empty>문의를 고르면 보상 후보와 결제 진단이 나옵니다.</Empty> : (
        <div className="grid gap-5 lg:grid-cols-2">
          <Result id="support-comp" title={`문의 #${current} 보상 후보`} res={comp}
            hint="주문의 배송 기록으로 정책표에 맞는 보상 쿠폰 후보를 냅니다. 요청은 사람이 승인한 뒤 따로 보냅니다.">
            {(d) => <Compensation s={d} />}
          </Result>
          <Result id="support-pay" title={`문의 #${current} 결제 진단`} res={pay}
            hint="이 문의의 주문에서 결제가 어디서 막혔는지 결제 기록으로 봅니다.">
            {(d) => <Payment d={d} />}
          </Result>
        </div>
      )}
    </div>
  );
}
