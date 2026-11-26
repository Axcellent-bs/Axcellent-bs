// 캐릭터 시트 칸 상태 (11/26 A). node --test src/lib/sheetState.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAKE_SHEET, REMAKE_SHEET, brandSheetState, sheetMissingBrand, sheetState } from "./sheetState.ts";

const ORIGINAL = { status: "done", source: "upload_original" };

test("만든 시트가 있으면 캐릭터 시트 + 다시 만들기, 원본은 옆에", () => {
  const s = sheetState({ sheet_image: { status: "done" }, original_image: ORIGINAL }, "virtual_human");
  assert.deepEqual(s, { title: "캐릭터 시트", hasSheet: true, originalPart: "original_image", button: REMAKE_SHEET });
  assert.ok(REMAKE_SHEET.includes("약 1.5 크레딧"));
});

test("원본만 있으면 '원본 사진 (시트 아직 없음)' + 시트 만들기", () => {
  const fresh = sheetState({ sheet_image: { status: "running" }, original_image: ORIGINAL }, "illustration");
  assert.equal(fresh.title, "원본 사진 (시트 아직 없음)");
  assert.equal(fresh.button, MAKE_SHEET);
  // 예전 기록: 원본이 시트 칸 자리 (source upload_original) → 시트가 아니다
  const legacy = sheetState({ sheet_image: ORIGINAL }, "virtual_human");
  assert.deepEqual(legacy, { title: "원본 사진 (시트 아직 없음)", hasSheet: false, originalPart: "sheet_image", button: MAKE_SHEET });
});

test("실사 사진 캐릭터·의뢰 기록은 버튼 없음 (지금 화면)", () => {
  assert.equal(sheetState({ sheet_image: { status: "done" } }, "real_photo").button, null);
  assert.equal(sheetState({ sheet_image: { status: "done" }, original_image: ORIGINAL }, "virtual_human", false).button, null);
  assert.equal(sheetState({}, "virtual_human").title, "캐릭터 시트");
});

test("회사 캐릭터 카드 (preview_part)", () => {
  assert.equal(brandSheetState("original_image", "virtual_human").button, MAKE_SHEET);
  assert.equal(brandSheetState("sheet_image", "virtual_human").title, "캐릭터 시트");
  assert.equal(brandSheetState("sheet_image", "real_photo").button, null);
  assert.equal(brandSheetState(null, "virtual_human").button, null);
});

test("의뢰 기록 sheet_missing → 회사 캐릭터 번호", () => {
  assert.equal(sheetMissingBrand({ sheet_image: { status: "done", original: true, sheet_missing: true, copied_from: "CR-aaaaaaaaaaaa" } }), "CR-aaaaaaaaaaaa");
  assert.equal(sheetMissingBrand({ sheet_image: { status: "done", copied_from: "CR-aaaaaaaaaaaa" } }), null);
});
