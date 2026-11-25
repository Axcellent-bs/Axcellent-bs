// node --test로 돈다 (npm test). 제작안 판 기록 (11/26 마케팅↔기획 재협업, lib/planReview.ts)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { reviewNote, reviewProblemLine, toPlanReview, versionCell } from "./planReview.ts";
import { skillKo } from "./skillNames.ts";

// 마케팅 plan_review.py가 의뢰 제작 기록 render.plan_review에 남기는 모양 그대로
const RAW = {
  versions: [
    { version: 1, passed: false, problems: [
      { kind: "duration", scene: 2, detail_ko: "장면 길이 2초 (영상 모델 최소 3초)" },
      { kind: "refs", scene: 3, detail_ko: "회사 캐릭터 말고 다른 인물이 가까이 나와요 (참조 이미지는 회사 캐릭터 1명뿐)" },
    ] },
    { version: 2, passed: true, problems: [] },
  ],
  requested: true, outcome: "revised", needs_human: false, summary_ko: "장면 2 장면 길이 2초 외 1건",
};

test("1판 마케팅 확인 ✕ 2개 → 2판 ✓ (사용 중) · 마케팅 요청으로 기획이 고침 · 1회", () => {
  const r = toPlanReview(RAW);
  assert.ok(r);
  const cells = r.versions.map((v, i) => versionCell(r, v, i));
  assert.deepEqual(cells.map((c) => [c.title, c.chip, c.ok]), [
    ["제작안 1판", "마케팅 확인 ✕ 2개", false],
    ["제작안 2판 (사용 중)", "마케팅 확인 ✓", true],
  ]);
  assert.equal(cells[1].note, "마케팅 요청으로 기획이 고침 · 1회");
  assert.equal(reviewProblemLine(r.versions[0].problems[0]), "장면 2 · 장면 길이 2초 (영상 모델 최소 3초)");
  assert.equal(reviewNote(r), "고치기 요청은 1회까지예요.");
});

test("2판도 ✕·기획 시간 초과·요청 실패면 사람 확인 필요", () => {
  const stuck = toPlanReview({ ...RAW, needs_human: true, versions: [RAW.versions[0], { version: 2, passed: false, problems: [RAW.versions[0].problems[0]] }] })!;
  assert.equal(stuck.needsHuman, true);
  assert.match(reviewNote(stuck), /사람 확인이 필요해요/);
  const late = toPlanReview({ ...RAW, outcome: "planning_timeout", needs_human: true, versions: [RAW.versions[0]] })!;
  assert.equal(versionCell(late, late.versions[0], 0).title, "제작안 1판 (사용 중)");
  assert.match(reviewNote(late), /제한 시간 안에.*1판으로 준비/);
  assert.match(reviewNote(toPlanReview({ ...RAW, outcome: "planning_revise_limit", versions: [RAW.versions[0]] })!), /요청하지 못해/);
});

test("모양이 틀리거나 없으면 그리지 않는다 (지금 화면 그대로)", () => {
  assert.equal(toPlanReview(undefined), null);
  assert.equal(toPlanReview({ versions: [] }), null);
  assert.equal(toPlanReview({ versions: [{ passed: true }] }), null);
});

test("대화방 공통 기록: revise_video_plan은 사람이 읽는 제목 '제작안 고치기'", () => {
  assert.equal(skillKo("revise_video_plan"), "제작안 고치기");
});

test("의뢰 카드 ⑦ 마케팅 준비(제작 기록 칸)에 판 기록", () => {
  const src = readFileSync(new URL("../components/office/RequestCreative.tsx", import.meta.url), "utf8");
  assert.match(src, /!!rec\?\.render\?\.plan_review && <div[^>]*><PlanVersions raw=\{rec\.render\.plan_review\} \/>/);
});
