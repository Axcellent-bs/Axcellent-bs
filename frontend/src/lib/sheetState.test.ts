// 캐릭터 시트 칸 상태 (11/26 A). node --test src/lib/sheetState.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAKE_SHEET, REMAKE_SHEET, brandSheetState, legacyOriginal, sheetMissingBrand, sheetState } from "./sheetState.ts";
import { readFileSync } from "node:fs";

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

test("11/27 운영 CR-b8a5525dda58: 합쳐 저장돼 원본 표시가 남은 시트도 execution_id가 있으면 캐릭터 시트", () => {
  // 운영 상태 그대로: 예전 원본 표시(source·original) + 우리가 그린 그림(execution_id·sha256)
  const merged = { status: "done", source: "upload_original", original: true, asset: "sheet.png", sha256: "ab12cd34ef", execution_id: "exe_1" };
  const s = sheetState({ sheet_image: merged, original_image: ORIGINAL }, "virtual_human");
  assert.deepEqual(s, { title: "캐릭터 시트", hasSheet: true, originalPart: "original_image", button: REMAKE_SHEET });
  assert.equal(legacyOriginal({ sheet_image: merged }), false);
  // 다시 그리면 표시가 지워진 모양(source higgsfield·original false)도 시트
  const fixed = { ...merged, source: "higgsfield", original: false };
  assert.equal(sheetState({ sheet_image: fixed, original_image: ORIGINAL }, "illustration").title, "캐릭터 시트");
  // execution_id가 없는 예전 원본은 지금처럼 원본
  assert.equal(legacyOriginal({ sheet_image: ORIGINAL }), true);
});

test("시트·스토리보드 그림 주소에 sha256이 ?v=로 붙는다 (다시 그려도 옛 그림을 안 보인다)", () => {
  // creativeRecords.ts는 화면 모듈을 불러 node --test가 못 읽는다 → 소스에서 본다
  const lib = readFileSync(new URL("./creativeRecords.ts", import.meta.url), "utf8");
  assert.match(lib, /\/asset\/\$\{part\}\$\{version \? `\?v=\$\{encodeURIComponent\(version\.slice\(0, 12\)\)\}` : ""\}/);
  const panel = readFileSync(new URL("../components/marketing/RenderPanel.tsx", import.meta.url), "utf8");
  // 시트·원본·스토리보드 그림 칸(Picture·OriginalThumb)이 sha256을 주소 버전으로 넘긴다
  assert.match(panel, /assetUrl\(recordId, playPart \?\? part, [^)]*info\?\.sha256/);
  assert.match(panel, /assetUrl\(recordId, part, info\.sha256\)/);
  const card = readFileSync(new URL("./creativeCardView.ts", import.meta.url), "utf8");
  assert.match(card, /version: part\?\.sha256 \?\? null/); // 대화방 카드 시트·스토리보드 그림
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
