import { Chip, Empty, ResourceBody, TONE, useResource } from "@/components/finance/bits";
import { won } from "@/lib/format";
import { support } from "@/lib/support";

// 보상한 고객: 이 사건에서 보상 쿠폰을 받은 고객을 보여 준다. 자료는 고객지원이 지급이 끝난 건만 모은 기록이다.
// 영향 고객과 맞춰 보는 일(누락 찾기)은 총괄이 영향 고객 목록을 함께 보내 `missing_buyer_ids`로 받는다.

export function IncidentCompensationList({ contextId, tick }: { contextId: string; tick: number }) {
  const res = useResource(`support-incident-compensation-${contextId}`, () => support.incidentCompensation(contextId), tick);
  return (
    <ResourceBody res={res}>
      {(list) => {
        const incident = list.incidents.find((i) => i.context_id === contextId);
        const buyers = incident?.buyers ?? [];
        return (
          <section aria-labelledby={`comp-${contextId}`} className="space-y-2">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <h3 id={`comp-${contextId}`} className="text-sm font-semibold">보상한 고객</h3>
              <p className="text-xs text-ink-500">
                쿠폰을 받은 고객 <b className="tabular-nums text-ink-900">{incident?.issued_buyers ?? 0}명</b> · 합계 <b className="tabular-nums text-ink-900">{won(incident?.issued_total ?? 0)}</b>
              </p>
            </div>
            {buyers.length === 0 ? (
              <Empty>이 사건에서 보상 쿠폰이 지급된 고객이 아직 없습니다. 보상이 요청돼 지급이 끝나면 여기에 나타납니다.</Empty>
            ) : (
              <div className="scroll-thin overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-sm">
                  <thead className="text-[11px] text-ink-500">
                    <tr><th className="pb-1 pr-3 font-medium">고객</th><th className="pb-1 pr-3 font-medium">쿠폰</th><th className="pb-1 pr-3 font-medium">내용</th><th className="pb-1 font-medium">상태</th></tr>
                  </thead>
                  <tbody>
                    {buyers.map((b) => (
                      <tr key={b.buyer_id} className="border-t border-ink-100 align-top">
                        <td className="whitespace-nowrap py-2 pr-3 font-medium tabular-nums">{b.buyer_id}</td>
                        <td className="whitespace-nowrap py-2 pr-3 tabular-nums text-ink-700">{b.coupon_id ?? "–"}</td>
                        <td className="whitespace-nowrap py-2 pr-3 tabular-nums text-ink-700">{b.count}장 × {won(b.unit_value)} = <b className="text-ink-900">{won(b.total_value)}</b></td>
                        <td className="whitespace-nowrap py-2">
                          {b.status === "issued"
                            ? <Chip label="지급됨" tone={TONE.ok} />
                            : <Chip label="이전 쿠폰에 이어짐" tone={TONE.info} />}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {incident?.missing_buyer_ids && incident.missing_buyer_ids.length > 0 && (
              <p role="status" className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">
                영향 고객 중 보상 기록이 없는 고객: {incident.missing_buyer_ids.join(", ")}
              </p>
            )}
            <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-ink-500">
              {list.notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
          </section>
        );
      }}
    </ResourceBody>
  );
}
