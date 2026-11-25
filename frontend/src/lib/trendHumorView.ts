// 보고서 "웃음 재료 · 상황과 형식"·"추천 형식 카드" 칸의 순수 계산 (components/data/TrendHumor.tsx). node --test (trendHumorView.test.ts).
// 상황 글·카드 내용은 서버 그대로. 예전 저장본(humor_status 없음)은 칸을 숨긴다
import type { HumorSourceKind, TrendFormatCard, TrendReport } from "./data.ts";

export const SOURCE_KIND: Record<HumorSourceKind, string> = {
  shorts_title: "쇼츠 제목 · 조회 상위",
  reels_caption: "릴스 캡션",
  hashtag: "해시태그",
};

/** 기본 영상 길이(초)로 구간 초 계산 (카드는 비율만 갖는다) */
export const CARD_SECONDS = 15;

export interface BeatChip { text: string; punch: boolean }

/** "기대 3초" 칩들 (15초 기준, 1초 미만은 1초) */
export function beatChips(card: Pick<TrendFormatCard, "beats">, total = CARD_SECONDS): BeatChip[] {
  return card.beats.map((b) => ({ text: `${b.label} ${Math.max(1, Math.round(b.ratio * total))}초`, punch: b.punch }));
}

/** 카드 한 줄 설명: "펀치 자막 · 인물 1명 · AI 영상 쉬움" (인물 0명이면 "손·사물만") */
export function cardLine(card: TrendFormatCard): string {
  const who = card.people === 0 ? "손·사물만" : `인물 ${card.people}명`;
  return `펀치 ${card.punchline} · ${who} · AI 영상 ${card.ai_difficulty}`;
}

export interface SignalChip { text: string; warn: boolean }

/** 형식 신호 칩: "Before & After 4", 새 신호는 "새로 N일 · 아직 추세 아님" 칩을 따로 */
export function signalChips(r: Pick<TrendReport, "format_signals">): SignalChip[] {
  const out: SignalChip[] = [];
  for (const s of r.format_signals ?? []) {
    out.push({ text: `${s.name} ${s.count}`, warn: false });
    if (s.new) out.push({ text: `${s.name} 새로 ${s.days_seen}일 · 아직 추세 아님`, warn: true });
  }
  return out;
}

export interface HumorView {
  status: NonNullable<TrendReport["humor_status"]>;
  reason: string | null;
  rows: { text: string; format: string; source: string }[];
  signals: SignalChip[];
  cards: TrendFormatCard[];
}

/** 형식 id → 이름 (추천 카드에 없으면 id 그대로, 모르면 "—") */
function formatName(id: string, cards: TrendFormatCard[]): string {
  if (!id) return "—";
  return cards.find((c) => c.id === id)?.name ?? id;
}

export function humorView(r: Pick<TrendReport, "humor_status" | "humor_reason" | "humor_situations" | "format_signals" | "recommended_cards">): HumorView | null {
  if (!r.humor_status) return null;
  const cards = r.recommended_cards ?? [];
  return {
    status: r.humor_status,
    reason: r.humor_reason ?? null,
    rows: (r.humor_situations ?? []).map((s) => ({ text: s.text, format: formatName(s.format_id, cards), source: SOURCE_KIND[s.source_kind] ?? s.source_kind })),
    signals: signalChips(r),
    cards,
  };
}
