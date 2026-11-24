// node --test로 돈다 (npm test). 시스템 관리 › 회사 연결 › 브랜드: 홍보 영상 방향 두 줄 (11/24).
import assert from "node:assert/strict";
import { test } from "node:test";
import { EXPOSURE_ROW, STYLE_ROW, canSave, exposureActive, hasScreenValue, sourceLine, type PromoState } from "./promoStyleView.ts";

const state = (over: Partial<PromoState> = {}): PromoState => ({
  promo_style: "fun_first", brand_exposure: "end_card",
  source: { promo_style: "tenant", brand_exposure: "tenant" },
  defaults: { promo_style: "fun_first", brand_exposure: "end_card" }, editable: true, ...over,
});

test("시안 문구: 두 줄과 선택지", () => {
  assert.equal(STYLE_ROW.label, "홍보 영상 방향");
  assert.deepEqual(STYLE_ROW.options.map((o) => o.label), ["재미 먼저 (브랜드는 끝에)", "제품·업종 소개"]);
  assert.equal(EXPOSURE_ROW.label, "브랜드 노출");
  assert.deepEqual(EXPOSURE_ROW.options.map((o) => o.value), ["end_card", "prop", "caption"]);
  assert.deepEqual(EXPOSURE_ROW.options.map((o) => o.label), ["마지막 2~3초 채널 이름·로고", "화면 속 소품으로 살짝", "캡션·프로필에만"]);
});

test("출처 한 줄과 되돌리기 버튼", () => {
  assert.equal(sourceLine(state()), "회사 설정 파일 값이에요");
  assert.equal(hasScreenValue(state()), false);
  const saved = state({ source: { promo_style: "screen", brand_exposure: "tenant" } });
  assert.equal(sourceLine(saved), "이 화면에서 저장한 값이에요");
  assert.equal(hasScreenValue(saved), true);
  assert.match(sourceLine(state({ source: { promo_style: "default", brand_exposure: "default" } })), /^기본값/);
});

test("저장 버튼: 값이 바뀌었고 저장할 수 있을 때만", () => {
  const now = { promo_style: "fun_first", brand_exposure: "end_card" } as const;
  assert.equal(canSave(state(), now), false); // 그대로
  assert.equal(canSave(state(), { ...now, brand_exposure: "prop" }), true);
  assert.equal(canSave(state(), { ...now, promo_style: "product" }, true), false); // 저장 중
  assert.equal(canSave(state({ editable: false }), { ...now, promo_style: "product" }), false);
  assert.equal(exposureActive("fun_first"), true);
  assert.equal(exposureActive("product"), false);
});
