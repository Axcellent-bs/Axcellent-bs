// 요즘 유행·밈(components/data/TrendMemes.tsx)의 순수 계산. node --test로 시험한다 (memeView.test.ts).
// 숫자는 서버 값 그대로 글자로만. judged_by가 share면 배수 자리에 몫(이번 기간 · 직전 기간 · 몫 배수)을 보인다
import type { TrendMeme } from "./data.ts";

/** 0~1 몫 → "1.2%" (0보다 크고 0.1% 미만이면 "0.1% 미만"). 모르면 null */
export function sharePct(v: number | null | undefined): string | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  const p = v * 100;
  if (p > 0 && p < 0.1) return "0.1% 미만";
  return `${p.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

/** 몫으로 판정한 유행인지 (예전 응답은 judged_by가 없어 영상 수 판정) */
export const judgedByShare = (m: Pick<TrendMeme, "judged_by">) => m.judged_by === "share";

/**
 * 몫 판정 줄: "이번 주 몫 1.2% · 지난주 0.4% (×3)". span·before는 화면의 기간 이름(이번 주/지난주, 어제/지난 4주 같은 요일).
 * 몫 판정이 아니면 null (화면은 지금처럼 영상 수 배수). 값이 없는 칸은 "모름", 몫 배수가 없으면 괄호를 뺀다
 */
export function shareLine(m: Pick<TrendMeme, "judged_by" | "share" | "previous_share" | "share_ratio">, span: string, before: string): string | null {
  if (!judgedByShare(m)) return null;
  const now = sharePct(m.share) ?? "모름";
  const prev = sharePct(m.previous_share) ?? "모름";
  const r = m.share_ratio;
  const ratio = typeof r === "number" && Number.isFinite(r) ? ` (×${r.toLocaleString("ko-KR", { maximumFractionDigits: 1 })})` : "";
  return `${span} 몫 ${now} · ${before} ${prev}${ratio}`;
}
