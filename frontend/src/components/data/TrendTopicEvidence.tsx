import { TONE } from "@/components/finance/bits";
import type { TrendReport } from "@/lib/data";
import { EXCLUDED_REASON, topicView } from "@/lib/trendTopicView";

/** 보고서 "의뢰 주제로 찾은 근거" (11/26): 확장어 칩 · 근거 표(근거·값·출처) · [뺀 근거 N개] 접기. 주제가 없으면 그리지 않는다 */
export function TrendTopicEvidence({ report }: { report: TrendReport }) {
  const v = topicView(report);
  if (!v) return null;
  return (
    <section className="card min-w-0 space-y-3 p-4 sm:p-5 trend-report-section" aria-label="의뢰 주제로 찾은 근거">
      <div>
        <h3 className="text-base font-semibold text-ink-900">의뢰 주제로 찾은 근거</h3>
        <p className="mt-1 text-[12.5px] text-ink-500">의뢰 주제 "{v.theme}"을 넓혀서 검색했어요. 주제와 상관없는 근거는 결론에서 뺐어요.</p>
      </div>
      <ul className="flex flex-wrap gap-2" aria-label="주제 확장어">
        <li><span className={`chip text-xs ${TONE.info}`}>{v.theme}</span></li>
        {v.terms.map((t) => <li key={t}><span className="chip text-xs">{t}</span></li>)}
      </ul>
      {v.rows.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[28rem] text-left text-[13px]">
            <thead><tr className="border-b border-ink-100 text-[11px] text-ink-500"><th className="px-2 py-1.5 font-medium">근거</th><th className="px-2 py-1.5 font-medium">값</th><th className="px-2 py-1.5 font-medium">출처</th></tr></thead>
            <tbody>
              {v.rows.map(({ row, chip }, i) => (
                <tr key={`${row.kind}-${row.label}-${i}`} className="border-b border-ink-100 align-top">
                  <td className="px-2 py-1.5 text-ink-900">
                    {row.label}
                    {chip && <span className={`chip ml-1.5 text-[11px] ${TONE.warn}`}>{chip}, 지금은 평소 수준</span>}
                  </td>
                  <td className={`whitespace-nowrap px-2 py-1.5 font-bold tabular-nums ${row.reference ? "text-ink-500" : "text-brand-700"}`}>{row.value}</td>
                  <td className="px-2 py-1.5 text-xs text-ink-500">{row.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-ink-500">주제와 이어지는 근거를 찾지 못했어요.</p>
      )}
      {v.excludedTitle && (
        <details className="border-t border-ink-100 pt-2">
          <summary className="cursor-pointer text-[13px] text-ink-700">{v.excludedTitle}</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-ink-500">
            {v.excluded.map((e, i) => (
              <li key={`${e.evidence_id ?? e.label}-${i}`}><span className="line-through">{e.label} {e.value}</span> — {EXCLUDED_REASON[e.reason] ?? e.reason}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
