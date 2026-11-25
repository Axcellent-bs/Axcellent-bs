// node --test로 돈다 (npm test). 웃음 재료 · 추천 형식 카드 칸 (11/26 E).
import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrendFormatCard } from "./data.ts";
import { beatChips, cardLine, humorView, signalChips } from "./trendHumorView.ts";

const card: TrendFormatCard = {
  id: "F-02", name: "기대 vs 현실", hook: "기대 라벨", punchline: "자막", people: 1, ai_difficulty: "쉬움", note: "",
  beats: [{ label: "기대", ratio: 0.2, punch: false }, { label: "현실", ratio: 0.25, punch: false }, { label: "펀치 리액션", ratio: 0.35, punch: true }, { label: "엔드카드", ratio: 0.2, punch: false }],
};

test("구간 칩: 15초 기준 초, 펀치 구간 표시", () => {
  assert.deepEqual(beatChips(card), [
    { text: "기대 3초", punch: false }, { text: "현실 4초", punch: false }, { text: "펀치 리액션 5초", punch: true }, { text: "엔드카드 3초", punch: false },
  ]);
  assert.equal(cardLine(card), "펀치 자막 · 인물 1명 · AI 영상 쉬움");
  assert.equal(cardLine({ ...card, people: 0 }), "펀치 자막 · 손·사물만 · AI 영상 쉬움");
});

test("형식 신호: 개수 칩 + 새 신호는 '새로 N일 · 아직 추세 아님'", () => {
  const chips = signalChips({ format_signals: [
    { name: "Before & After", count: 4, days_seen: 5, new: false, format_id: "F-05" },
    { name: "Listicle", count: 2, days_seen: 1, new: true, format_id: "F-04" },
  ] });
  assert.deepEqual(chips.map((c) => c.text), ["Before & After 4", "Listicle 2", "Listicle 새로 1일 · 아직 추세 아님"]);
  assert.deepEqual(chips.map((c) => c.warn), [false, false, true]);
});

test("칸: 예전 저장본은 숨김, 상황 행은 형식 이름·출처 종류만, 실패는 이유", () => {
  assert.equal(humorView({}), null);
  const v = humorView({
    humor_status: "known", humor_reason: null, recommended_cards: [card], format_signals: [],
    humor_situations: [{ text: "시험 전날 계획표 vs 새벽 3시", format_id: "F-02", source_kind: "shorts_title" }, { text: "딱 5분 쉬었는데 아침", format_id: "", source_kind: "hashtag" }],
  })!;
  assert.deepEqual(v.rows, [
    { text: "시험 전날 계획표 vs 새벽 3시", format: "기대 vs 현실", source: "쇼츠 제목 · 조회 상위" },
    { text: "딱 5분 쉬었는데 아침", format: "—", source: "해시태그" },
  ]);
  const failed = humorView({ humor_status: "unknown", humor_reason: "LLM 호출 실패 (RuntimeError), 상황을 뽑지 못함", humor_situations: [] })!;
  assert.equal(failed.rows.length, 0);
  assert.match(failed.reason ?? "", /LLM 호출 실패/);
});
