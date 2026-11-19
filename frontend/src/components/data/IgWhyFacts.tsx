import { factLabel, factText, factTone } from "@/lib/igWhy";
import type { InstagramWhyFact } from "@/lib/data";

// 인스타 "왜 떴나"(레이더 묶음)·"왜 뜨나"(인기 오디오) 근거 표: 라벨 | 값 (평소 값, ×배수).
// 숫자는 서버 코드 값 그대로. 공식 API로 볼 수 없는 줄(unknown)은 주의색, 값을 모르는 줄은 회색 + 이유

const TONE_CLASS = { caution: "text-amber-700", muted: "text-ink-500", plain: "text-ink-900" } as const;

export function IgWhyFacts({ facts }: { facts: InstagramWhyFact[] }) {
  if (facts.length === 0) return <p className="text-xs text-ink-500">근거 숫자가 아직 없어요.</p>;
  return (
    <table className="w-full border-collapse text-xs">
      <tbody>
        {facts.map((f, i) => {
          const tone = factTone(f);
          return (
            <tr key={`${i}-${f.kind}-${f.label}`} className={`border-b border-ink-100 align-top ${tone === "caution" ? "bg-amber-50" : ""}`}>
              <th scope="row" className={`w-[45%] px-1.5 py-1 text-left font-medium ${tone === "caution" ? "text-amber-700" : "text-ink-500"}`}>{factLabel(f)}</th>
              <td className={`px-1.5 py-1 tabular-nums ${TONE_CLASS[tone]}`}>{factText(f)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
