// 의뢰 1단계 등장 캐릭터 여럿 (11/27 B). node --test src/lib/requestCast.test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { MAX_CAST, addCast, canAddCast, castLine, castPayload, chatCharacterLine, removeCast, setCastRole, type CastPick, type CharacterRow } from "./requestCharacter.ts";

const A = "CR-aaaaaaaaaaaa", B = "CR-bbbbbbbbbbbb", C = "CR-cccccccccccc", D = "CR-dddddddddddd";

test("3명까지 고르고 4명째는 막힌다 (같은 캐릭터는 한 번)", () => {
  let list: CastPick[] = [];
  for (const id of [A, B, C, D, A]) list = addCast(list, id);
  assert.deepEqual(list.map((m) => m.record_id), [A, B, C]);
  assert.equal(MAX_CAST, 3);
  assert.equal(canAddCast(list), false);
  assert.equal(list[0].role_ko, "주인공"); // 첫 번째 = 주인공
  assert.deepEqual(removeCast(list, B).map((m) => m.record_id), [A, C]);
  assert.equal(canAddCast(removeCast(list, B)), true);
});

test("역할 저장 (20자까지, 보낼 때 앞뒤 공백 뗌)", () => {
  let list = addCast(addCast([], A), B);
  list = setCastRole(list, B, " 동료 ");
  list = setCastRole(list, A, "가".repeat(25));
  assert.equal(list[0].role_ko.length, 20);
  assert.deepEqual(castPayload(list), { recordId: A, cast: [{ record_id: A, role_ko: "가".repeat(20) }, { record_id: B, role_ko: "동료" }] });
});

test("한 명이면 예전과 같은 요청, 없으면 null", () => {
  assert.deepEqual(castPayload(addCast([], A)), { recordId: A });
  assert.deepEqual(castPayload([]), { recordId: null });
});

test("1단계 완료 줄: 등장 캐릭터 2 · 주인공 여성(…) · 동료 남성(…), 한 명이면 지금 줄", () => {
  const row = {
    plan: [{ step_id: "s0", dept: null, skill: "brand_character", label: "회사 캐릭터", after: [] },
      { step_id: "prepare", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 초안 준비", after: ["s0"] }],
    steps: {}, status: "running", task: "promo_video", brand_character_record_id: A,
    cast: [{ record_id: A, role_ko: "주인공" }, { record_id: B, role_ko: "동료" }],
  } as unknown as CharacterRow;
  const names: Record<string, string> = { [A]: "여성", [B]: "남성" };
  assert.equal(chatCharacterLine(row, "여성", (id) => names[id])?.text, `등장 캐릭터 2 · 주인공 여성(${A}) · 동료 남성(${B})`);
  assert.equal(castLine(row.cast, () => null), `등장 캐릭터 2 · 주인공 ${A} · 동료 ${B}`);
  const one = { ...row, cast: undefined } as CharacterRow;
  assert.equal(chatCharacterLine(one, "여성")?.text, `회사 캐릭터: 여성(${A})`);
});

test("화면: 2명 이상이면 역할 입력·×빼기·[이 캐릭터들로 만들기], 추가 버튼은 3명이면 숨김", () => {
  const src = readFileSync(new URL("../components/office/BrandCharacterAsk.tsx", import.meta.url), "utf8");
  assert.match(src, /cast\.length <= 1 &&/); // 한 명: 지금 화면 그대로
  assert.match(src, /이 캐릭터들로 만들기/);
  assert.match(src, /\+ 회사 캐릭터에서 추가/);
  assert.match(src, /\+ 이미지 올려 추가/);
  assert.match(src, /canAddCast\(cast\) && \(/); // 3명이면 추가 버튼 숨김
  assert.match(src, /빼기`\}/);
});
