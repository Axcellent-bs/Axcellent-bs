import { TONE } from "@/components/finance/bits";
import type { TrendReport } from "@/lib/data";
import { igWarning, sampleView } from "@/lib/trendSampleView";

/** 보고서 맨 위 경고 줄 (11/26): 인스타 호출 0번 · 연결 안 됨 */
export function IgWarningBanner({ report }: { report: TrendReport }) {
  const w = igWarning(report);
  if (!w) return null;
  return (
    <div role="status" className={`flex items-start gap-2.5 px-4 py-2.5 text-sm ${TONE.warn}`}>
      <span aria-hidden="true">⚠</span>
      <p><b className="block">{w.title}</b>{w.body}</p>
    </div>
  );
}

/** 표본 · 레이더 계정 (11/26): 전체 · 대상 맞음 · 사람 확인 칩과 한 줄 */
export function TrendSample({ report }: { report: TrendReport }) {
  const v = sampleView(report);
  if (!v) return null;
  const tone = { plain: "", brand: TONE.info, warn: TONE.warn } as const;
  return (
    <section className="card min-w-0 space-y-2 p-4 sm:p-5 trend-report-section" aria-label="표본">
      <h3 className="text-base font-semibold text-ink-900">표본 · 레이더 계정</h3>
      <ul className="flex flex-wrap gap-2">
        {v.chips.map((c) => <li key={c.text}><span className={`chip text-xs ${tone[c.tone]}`}>{c.text}</span></li>)}
      </ul>
      {v.notes.map((n) => <p key={n} className="text-[12.5px] text-ink-500">{n}</p>)}
    </section>
  );
}
