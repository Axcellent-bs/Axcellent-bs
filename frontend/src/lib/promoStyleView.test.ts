// node --test로 돈다 (npm test). 마케팅 › 브랜드(/marketing/brand): 홍보 영상 방향 두 줄 (11/24).
import assert from "node:assert/strict";
import { test } from "node:test";
import { EXPOSURE_ROW, LOOK_NOTICE, STYLE_ROW, errText, canSave, exposureActive, hasScreenValue, lookDirty, lookDraft, lookError, lookShown, saveBody, sourceLabel, sourceLine, type PromoState } from "./promoStyleView.ts";

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

const withLook = (over: Partial<PromoState> = {}) => state({
  character_ko: "원본 캐릭터", character_en: "original characters", setting_ko: "", setting_en: "", style_en: "flat style",
  source: { promo_style: "tenant", brand_exposure: "tenant", character_ko: "screen", character_en: "tenant", setting_ko: "default", setting_en: "default", style_en: "default" },
  ...over,
});
const pick0 = { promo_style: "fun_first", brand_exposure: "end_card" } as const;

test("영상 기본 모습: 새 칸이 없으면 숨기고 저장 본문은 예전 그대로", () => {
  const old = state();
  assert.equal(lookShown(old), false);
  assert.equal(lookDirty(old, lookDraft(old)), false);
  assert.deepEqual(saveBody(old, pick0, lookDraft(old)), { promo_style: "fun_first", brand_exposure: "end_card" });
  assert.equal(hasScreenValue(old), false);
});

test("영상 기본 모습: 보이고 출처 칩, 기본값 출처는 입력을 비운다", () => {
  const s = withLook();
  assert.equal(lookShown(s), true);
  assert.equal(sourceLabel(s.source.character_ko), "화면 저장");
  assert.equal(sourceLabel(s.source.character_en), "회사 설정");
  assert.equal(sourceLabel(s.source.style_en), "기본값");
  assert.equal(sourceLabel(undefined), "");
  const d = lookDraft(s);
  assert.equal(d.character_ko, "원본 캐릭터");
  assert.equal(d.style_en, "");
  assert.equal(hasScreenValue(s), true);
  assert.equal(canSave(s, pick0, false, lookDirty(s, d)), false);
  assert.equal(canSave(s, pick0, false, lookDirty(s, { ...d, setting_en: "city" })), true);
});

test("영상 기본 모습: 저장 본문에 새 칸이 들어간다", () => {
  const s = withLook();
  const body = saveBody(s, pick0, { ...lookDraft(s), style_en: "flat" });
  assert.deepEqual(Object.keys(body).sort(), ["brand_exposure", "character_en", "character_ko", "promo_style", "setting_en", "setting_ko", "style_en"]);
  assert.equal(body.style_en, "flat");
});

test("영상 기본 모습: 200자 제한", () => {
  const d = lookDraft(withLook());
  assert.equal(lookError({ ...d, style_en: "a".repeat(200) }), null);
  assert.match(lookError({ ...d, style_en: "a".repeat(201) }) ?? "", /200자/);
});

test("422: 서버 문장을 그대로 보여 준다", () => {
  assert.equal(errText(new Error("실존 인물 이름은 쓸 수 없어요")), "실존 인물 이름은 쓸 수 없어요");
  assert.equal(errText("x"), "처리하지 못했어요");
});

test("영상 기본 모습: 안내 문구", () => {
  assert.equal(LOOK_NOTICE, "의뢰에서 캐릭터를 고르면 그 캐릭터가 먼저예요. 여기는 고르지 않았을 때 기본값이에요.");
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
