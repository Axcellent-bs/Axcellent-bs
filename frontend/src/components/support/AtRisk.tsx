import { useState } from "react";
import { Bars, Stat } from "@/components/admin/bits";
import { ChartWithTable, TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, RefList, TONE, Waiting, useResource } from "@/components/finance/bits";
import { humanId, shortDateTime } from "@/lib/format";
import { ACTION_KO, LEVEL_KO, REASON_KO, UNKNOWN, ko, support, type CustomerSignals } from "@/lib/support";

// 위험 고객: 이탈·재문의 위험이 기준 단계 이상인 구매자(점수 높은 순)와, 고른 구매자의 위험 신호.
// 단계·점수는 support가 센 값 그대로다.

const LevelChip = ({ level }: { level: string }) => {
  const l = LEVEL_KO[level] ?? { label: level, tone: "muted" as const };
  return <Chip label={l.label} tone={TONE[l.tone]} />;
};

function Detail({ buyerId, tick }: { buyerId: string; tick: number }) {
  const res = useResource(`signals-${buyerId}`, () => support.signals(buyerId), tick);
  if (!res.env) return res.error ? <ErrorBox message={`위험 신호를 받지 못했습니다. ${res.error}`} onRetry={res.reload} /> : <Waiting />;
  const s = res.env.data;
  const bars: Record<string, number> = {};
  s.reasons.forEach((r) => { bars[ko(REASON_KO, r.code)] = r.points; });
  return (
    <Panel id="support-signals" title={`${humanId(buyerId)}의 위험 신호`} meta={res.env.meta}
      hint="이유별 점수를 더한 값이 위험 점수입니다. 점수와 단계는 문의·클레임 기록에서 세어 나온 값입니다."
      tools={<LevelChip level={s.level} />}>
      {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
      <div className="space-y-4">
        <div className="flex flex-wrap gap-3">
          <Stat label="위험 점수" value={s.score} />
          <Stat label="권장 조치" value={<span className="text-base">{ko(ACTION_KO, s.recommended_action)}</span>} />
          <Stat label="마지막 문의" value={<span className="text-base">{shortDateTime(s.last_contact_at)}</span>} />
        </div>
        {s.claim_pattern_review && (
          <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">클레임이 잦아 사람이 패턴을 한 번 살펴볼 필요가 있습니다.</p>
        )}
        <div>
          <p className="mb-2 text-xs font-medium text-ink-700">이유별 점수</p>
          {s.reasons.length === 0 ? <p className="text-xs text-ink-500">점수가 붙은 이유가 없습니다.</p> : (
            <ChartWithTable
              chart={<Bars data={bars} color="bg-red-500" />}
              table={<TableView columns={["이유", "건수", "점수"]} rows={s.reasons.map((r) => [ko(REASON_KO, r.code), `${r.count}건`, `${r.points}점`])} />}
            />
          )}
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-ink-700">최근 기록 집계</p>
          <TableView columns={["항목", "건수"]} rows={Object.entries(s.counts).map(([k, v]) => [ko(REASON_KO, k), `${v}건`])} />
        </div>
        <RefList refs={[s.buyer_id]} label="고객 번호 보기" />
      </div>
    </Panel>
  );
}

export function AtRisk({ tick, selected, onSelect }: { tick: number; selected: string | null; onSelect: (id: string) => void }) {
  const [min, setMin] = useState<"medium" | "high">("medium");
  const list = useResource(`at-risk-${min}`, () => support.atRisk(min), tick);
  if (!list.env) return list.error ? <ErrorBox message={`위험 고객 목록을 받지 못했습니다. ${list.error}`} onRetry={list.reload} /> : <Waiting />;
  const items: CustomerSignals[] = list.env.data;
  const total = (list.env.meta as { total?: number }).total ?? items.length;
  const highCount = items.filter((c) => c.level === "high").length;
  const current = items.find((c) => c.buyer_id === selected)?.buyer_id ?? selected ?? items[0]?.buyer_id ?? null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-3">
        <Stat label="위험 고객" value={`${total}명`} sub={items.length < total ? `점수 높은 ${items.length}명만 표시` : undefined} tone={total ? "text-accent" : ""} />
        <Stat label="그중 위험 높음" value={`${highCount}명`} tone={highCount ? "text-red-600" : ""} />
      </div>
      <Panel id="support-at-risk" title="위험 고객 목록" meta={list.env.meta}
        hint="이탈하거나 다시 문의할 가능성이 큰 고객입니다. 점수가 높은 순서이며, 고르면 아래에 이유가 나옵니다."
        tools={
          <div role="group" aria-label="위험 단계 기준" className="flex gap-1 rounded-full bg-surface p-1 ring-1 ring-ink-100">
            {([["medium", "보통 이상"], ["high", "높음만"]] as const).map(([v, label]) => (
              <button key={v} type="button" aria-pressed={min === v} onClick={() => setMin(v)}
                className={`rounded-full px-3 py-1 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${min === v ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>{label}</button>
            ))}
          </div>
        }>
        {items.length === 0 ? <Empty>기준에 맞는 위험 고객이 없습니다.</Empty> : (
          <ul className="space-y-2">
            {items.map((c) => {
              const on = c.buyer_id === current;
              return (
                <li key={c.buyer_id}>
                  <button type="button" aria-pressed={on} onClick={() => onSelect(c.buyer_id)}
                    className={`flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl px-4 py-3 text-left ring-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-50 ring-brand-500" : "bg-surface-2 ring-ink-100 hover:bg-ink-100"}`}>
                    <span className="text-sm font-semibold text-ink-900">{humanId(c.buyer_id)}</span>
                    <LevelChip level={c.level} />
                    <span className="text-xs text-ink-500">{ko(ACTION_KO, c.recommended_action)}</span>
                    <span className="ml-auto text-xs tabular-nums text-ink-500">마지막 문의 {shortDateTime(c.last_contact_at)}</span>
                    <span className="w-14 text-right text-sm font-semibold tabular-nums">{c.score}점</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
      {current ? <Detail buyerId={current} tick={tick} /> : <Panel title="위험 신호"><Empty>고객을 고르면 이유가 나옵니다 ({UNKNOWN}).</Empty></Panel>}
    </div>
  );
}
