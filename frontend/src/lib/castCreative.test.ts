// 제작 기록 등장 캐릭터별 칸·영상 참조 안내 (11/27 D). node --test src/lib/castCreative.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { CAST_SINGLE_REF_KO, castRefLine } from "./creativeVideo.ts";
import { MAKE_SHEET, castLeadRole, castSlots } from "./sheetState.ts";

const MEMBERS = { members: [{ record_id: "CR-aaaaaaaaaaaa", role_ko: "주인공" }, { record_id: "CR-bbbbbbbbbbbb", role_ko: "동료" },
  { record_id: "CR-cccccccccccc", role_ko: "마스코트" }] };

test("캐릭터별 칸: 역할 · 시트/원본, 원본이면 [캐릭터 시트 만들기 · 약 1.5]", () => {
  const render = {
    cast: MEMBERS,
    cast_1: { status: "done", role_ko: "동료", character_id: "CR-bbbbbbbbbbbb", picture: "sheet", asset: "cast_1.png", sha256: "s" },
    cast_2: { status: "done", role_ko: "마스코트", character_id: "CR-cccccccccccc", picture: "original", asset: "cast_2.jpg", sha256: "o" },
  };
  assert.equal(castLeadRole(render), "주인공");
  assert.deepEqual(castSlots(render).map((s) => [s.title, s.button, s.characterId]), [
    ["동료 · 캐릭터 시트", null, "CR-bbbbbbbbbbbb"],
    ["마스코트 · 원본 사진 (시트 아직 없음)", MAKE_SHEET, "CR-cccccccccccc"],
  ]);
  assert.ok(MAKE_SHEET.includes("약 1.5 크레딧"));
});

test("한 명·없음이면 칸 없음 (지금 화면 그대로)", () => {
  assert.deepEqual(castSlots({}), []);
  assert.equal(castLeadRole({ cast: { members: [MEMBERS.members[0]] } }), null);
  assert.deepEqual(castSlots({ cast: { members: [MEMBERS.members[0]] }, cast_1: { status: "done" } }), []);
});

test("영상 참조 안내: 참조 1장 모델이면 주인공만 + 한 줄, 아니면 몇 명 참조", () => {
  assert.equal(castRefLine({ cast: 3, cast_refs: 1, cast_single_ref: true }), CAST_SINGLE_REF_KO);
  assert.equal(CAST_SINGLE_REF_KO, "이 영상 모델은 캐릭터 1개만 참조해요");
  assert.equal(castRefLine({ cast: 3, cast_refs: 3, cast_single_ref: false }), "캐릭터 3명 참조");
  assert.equal(castRefLine({ model: "x" }), null);
  assert.equal(castRefLine(null), null);
});
