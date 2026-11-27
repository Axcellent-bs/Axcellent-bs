// 제작안 "등장 캐릭터" 줄 (11/27 C). node --test src/lib/videoPlanCast.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { castLineKo, toVideoPlan } from "./videoPlan.ts";

const RAW = {
  concept: "겨울 독서", duration_s: 15, aspect: "9:16", script: "s",
  scenes: [{ n: 1, start_s: 0, end_s: 15, visual: "v", caption: "", prompt: "p" }],
  style: "st", negative: "ng", written_by: "llm", character_source: "company_character", character_ko: "검은 긴 생머리",
};
const CAST = [
  { record_id: "CR-aaaaaaaaaaaa", role_ko: "주인공", summary_ko: "검은 긴 생머리·원형 안경", look: "photo", reason: "" },
  { record_id: "CR-bbbbbbbbbbbb", role_ko: "동료", summary_ko: "", look: "", reason: "read_failed" },
];

test("cast를 읽고 줄 글은 역할 · 외형 · 그림체, 못 읽은 것은 이유", () => {
  const v = toVideoPlan({ ...RAW, cast: CAST });
  assert.ok(v?.cast);
  assert.deepEqual(v.cast.map(castLineKo), ["주인공 · 검은 긴 생머리·원형 안경 · 실사 그대로", "동료 · 못 읽음"]);
});

test("cast가 없거나 한 명이면 칸 없음 (지금 주인공 줄 그대로), 모양이 틀린 줄은 버린다", () => {
  assert.equal(toVideoPlan(RAW)?.cast, undefined);
  assert.equal(toVideoPlan({ ...RAW, cast: CAST.slice(0, 1) })?.cast, undefined);
  assert.equal(toVideoPlan({ ...RAW, cast: [...CAST, { record_id: "x" }] })?.cast?.length, 2);
});
