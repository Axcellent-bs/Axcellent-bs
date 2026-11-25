import { TONE } from "@/components/finance/bits";
import type { TrendReport } from "@/lib/data";
import { CARD_SECONDS, beatChips, cardLine, humorView } from "@/lib/trendHumorView";

/** 보고서 "웃음 재료 · 상황과 형식" + "추천 형식 카드" (11/26). 상황은 요약만(원문·영상은 보여 주지 않는다) */
export function TrendHumor({ report }: { report: TrendReport }) {
  const v = humorView(report);
  if (!v) return null;
  return (
    <>
      <section className="card min-w-0 space-y-3 p-4 sm:p-5 trend-report-section" aria-label="웃음 재료">
        <div>
          <h3 className="text-base font-semibold text-ink-900">웃음 재료 · 상황과 형식</h3>
          <p className="mt-1 text-[12.5px] text-ink-500">공식 API로 받은 제목·캡션·해시태그에서 <b>상황만</b> 요약했어요. 원문 문구와 영상은 기획에 넘기지 않아요.</p>
        </div>
        {v.rows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[28rem] text-left text-[13px]">
              <thead><tr className="border-b border-ink-100 text-[11px] text-ink-500"><th className="px-2 py-1.5 font-medium">상황 (요약)</th><th className="px-2 py-1.5 font-medium">형식 추정</th><th className="px-2 py-1.5 font-medium">근거</th></tr></thead>
              <tbody>
                {v.rows.map((r, i) => (
                  <tr key={`${r.text}-${i}`} className="border-b border-ink-100 align-top">
                    <td className="px-2 py-1.5 text-ink-900">{r.text}</td>
                    <td className="px-2 py-1.5">{r.format === "—" ? <span className="text-ink-400">—</span> : <span className="chip text-[11px]">{r.format}</span>}</td>
                    <td className="px-2 py-1.5 text-xs text-ink-500">{r.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-ink-500">{v.reason ?? "상황 요약이 없어요."}</p>
        )}
        {v.signals.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-500">이번 주 형식 신호 (인기 오디오 이름, 보조 신호)</span>
            {v.signals.map((s) => <span key={s.text} className={`chip text-xs ${s.warn ? TONE.warn : ""}`}>{s.text}</span>)}
          </div>
        )}
      </section>
      {v.cards.length > 0 && (
        <section className="card min-w-0 space-y-3 p-4 sm:p-5 trend-report-section" aria-label="추천 형식 카드">
          <div>
            <h3 className="text-base font-semibold text-ink-900">추천 형식 카드</h3>
            <p className="mt-1 text-[12.5px] text-ink-500">우리가 정리한 형식 라이브러리에서 상황과 형식 신호에 맞는 카드를 골랐어요. 구간 초는 {CARD_SECONDS}초 기준이에요.</p>
          </div>
          <ul className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {v.cards.map((c, i) => (
              <li key={c.id} className={`flex min-w-0 flex-col gap-1.5 border p-3 ${i === 0 ? "border-brand-500 bg-brand-50" : "border-ink-100"}`}>
                <span className="font-bold text-ink-900">{c.id} {c.name}{i === 0 && <span className={`chip ml-1.5 text-[11px] ${TONE.info}`}>추천</span>}</span>
                <span className="flex flex-wrap gap-1" aria-label="구간">
                  {beatChips(c).map((b, n) => (
                    <span key={`${b.text}-${n}`} className={`px-1.5 py-px text-[11.5px] ${b.punch ? "bg-brand-600 font-semibold text-white" : "bg-ink-100 text-ink-700"}`}>{b.text}{b.punch && <span className="sr-only"> (펀치라인)</span>}</span>
                  ))}
                </span>
                {c.hook && <span className="text-xs text-ink-700">훅: {c.hook}</span>}
                <span className="text-xs text-ink-500">{cardLine(c)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
