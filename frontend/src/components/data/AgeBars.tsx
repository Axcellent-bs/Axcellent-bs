import type { AgeBandTrend } from "@/lib/data";

// 연령대별 평소 대비 배수 막대 (trend_age 결과 그대로, 설정 순서). 인사이트 카드·기회 가설 카드가 같이 쓴다.
// 막대 길이는 그중 가장 큰 배수에 대한 비율(그리기용). top = 굵게 보일 연령대(타깃 연령대). 급상승이면 파랑

const num = (v: number) => v.toLocaleString("ko-KR", { maximumFractionDigits: 2 });

export function AgeBars({ bands: all, top }: { bands: AgeBandTrend[]; top: string | null }) {
  const bands = all.filter((b) => typeof b.ratio === "number");
  const max = Math.max(0, ...bands.map((b) => b.ratio ?? 0));
  if (bands.length === 0) return null;
  return (
    <div className="mt-2 space-y-1.5">
      <ul className="space-y-1">
        {bands.map((b) => (
          <li key={b.band} className="grid grid-cols-[3.5rem_minmax(0,1fr)_5.5rem] items-center gap-2 text-xs">
            <span className={b.band === top ? "font-semibold text-ink-900" : "text-ink-700"}>{b.band}</span>
            <span className="h-2 rounded-full bg-ink-100" aria-hidden>
              <span className={`block h-2 rounded-full ${b.status === "rising" ? "bg-brand-500" : "bg-ink-300"}`}
                style={{ width: `${max > 0 ? Math.max(2, ((b.ratio ?? 0) / max) * 100) : 0}%` }} />
            </span>
            <span className="text-right tabular-nums text-ink-700">{num(b.ratio ?? 0)}배</span>
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-ink-500">연령대끼리 양 비교 아님</p>
    </div>
  );
}
