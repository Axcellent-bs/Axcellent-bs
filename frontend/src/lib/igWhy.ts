// 인스타 "왜 떴나·왜 뜨나" 근거 표의 글자 (화면은 components/data/IgWhyFacts.tsx). 숫자는 서버 값 그대로 쓴다.

import type { InstagramWhyFact } from "./data.ts";

/** "값 (평소 값, ×배수)". 값을 모르면 이유. 평소·배수가 없으면 그 부분만 뺀다 */
export function factText(f: InstagramWhyFact): string {
  if (f.kind === "unknown") return f.reason ?? "확인 불가";
  if (f.value === null) return f.reason ? `모름 · ${f.reason}` : "모름";
  const extra = [
    f.baseline !== null ? `평소 ${f.baseline}` : null,
    f.ratio !== null ? `×${f.ratio.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}` : null,
  ].filter(Boolean);
  const tail = extra.length ? ` (${extra.join(", ")})` : "";
  return `${f.value}${tail}${f.reason ? ` · ${f.reason}` : ""}`;
}

/** unknown 줄(공식 API로 볼 수 없는 것)은 주의색, 값을 모르는 줄은 회색 */
export function factTone(f: InstagramWhyFact): "caution" | "muted" | "plain" {
  if (f.kind === "unknown") return "caution";
  return f.value === null ? "muted" : "plain";
}

/** unknown 줄은 라벨과 이유가 같은 문장이라 라벨 칸에 "확인 불가"만 쓴다 */
export function factLabel(f: InstagramWhyFact): string {
  if (f.kind !== "unknown") return f.label;
  const head = f.label.split(":")[0]?.trim();
  return head && head !== f.label ? head : "확인 불가";
}
