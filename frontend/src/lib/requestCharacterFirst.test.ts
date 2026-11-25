// node --test로 돈다 (npm test). 11/24 홍보 영상 의뢰는 회사 캐릭터 답을 먼저 받고 데이터분석을 시작한다.
// 총괄 계획 맨 앞 사람 단계 character(skill brand_character)가 답 대기(waiting_human · character_wait)로 기다린다.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  PLAN_OUTDATED_LINE, SPACE_NOTE, askMode, chatAskShown, characterPending, planOutdated, unansweredNotice, type CharacterRow,
} from "./requestCharacter.ts";
import { stepRows } from "./requestCards.ts";
import { requestDesks } from "./officeRequests.ts";
import { CHARACTER_WAIT, canDecide, type StepStatus, type WorkRequestRow, type WorkRequestStep } from "./workRequests.ts";

const step = (status: StepStatus, extra: Partial<WorkRequestStep> = {}): WorkRequestStep => ({
  status, attempts: 0, grade: null, note: "", reason_code: null, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null, ...extra,
});
const PLAN = [
  { step_id: "character", dept: null, skill: "brand_character", label: "회사 캐릭터 고르기", after: [] },
  { step_id: "trend", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: ["character"] },
  { step_id: "insight", dept: "data", skill: "trend_report_insight", label: "기획에 넘길 인사이트", after: ["trend"] },
  { step_id: "plan", dept: "planning", skill: "receive_insight", label: "영상 제작안", after: ["insight"] },
  { step_id: "confirm", dept: null, skill: "human_check", label: "사람 확인", after: ["plan"] },
  { step_id: "prepare", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 초안 준비", after: ["confirm"] },
  { step_id: "make", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["prepare"] },
];
const WAIT = step("waiting_human", { reason_code: CHARACTER_WAIT, note: "캐릭터 답 대기" });
const pendingAll = Object.fromEntries(PLAN.map((p) => [p.step_id, step("pending")]));
const waitingRow = (extra: Partial<WorkRequestRow> = {}): WorkRequestRow => ({
  request_id: "wr_aa0669b6f7", account: "a", text: "홍보 영상", created_at: "2026-11-24T01:00:00Z", updated_at: "2026-11-24T01:00:00Z",
  status: "waiting", task: "promo_video", confidence: 0.9, reason: null, alternatives: [], classification: null,
  plan: PLAN, steps: { ...pendingAll, character: WAIT }, log: [], ...extra,
});
const SAVED = [{ record_id: "CR-1027eb75aaaa", name: "지난번 캐릭터", saved_at: "2026-11-20T03:00:00Z", is_default: true }];

test("단계 목록 첫 줄은 '회사 캐릭터 고르기'이고 답 대기 칩, 승인·거절 버튼은 없다", () => {
  const rows = stepRows(waitingRow());
  assert.equal(rows[0].label, "회사 캐릭터 고르기");
  assert.equal(rows[0].chip.label, "답 대기");
  assert.equal(rows[0].dept, null);
  assert.equal(rows[0].decide, null); // decide가 아니라 캐릭터 답으로만 닫힌다
  assert.equal(canDecide(WAIT), false);
  assert.equal(rows[1].chip.label, "대기"); // 데이터분석은 아직 시작 전
});

test("답 대기면 질문 상자를 펼친다: 저장 캐릭터가 있으면 3버튼(saved), 없으면 있어요·없어요(ask)", () => {
  const r = waitingRow();
  assert.equal(characterPending(r), true);
  assert.equal(chatAskShown(r), true);
  assert.equal(askMode(r, SAVED), "saved"); // [이 캐릭터로 만들기][다른 이미지 올리기][캐릭터 없이]
  assert.equal(askMode(r, []), "ask"); // [있어요][없어요 (AI가 새로 그려요)]
  assert.equal(unansweredNotice(r, "confirm"), null); // 사람 확인 단계까지 가지 않았다
});

test("새 안내 문구: '답하지 않으면 캐릭터 없이'를 빼고 답해야 다음 단계가 시작된다", () => {
  // 11/26 예전 기대값: "… 영상 초안 준비 전까지는 바꿀 수 있어요."
  assert.ok(SPACE_NOTE.endsWith("답해야 다음 단계가 시작돼요. 영상 요청 전까지는 바꿀 수 있어요."));
  assert.ok(!SPACE_NOTE.includes("답하지 않으면"));
});

test("답하면 단계가 완료되고 질문이 접힌 한 줄이 된다 · 멈춘 의뢰는 더 기다리지 않는다", () => {
  const answered = waitingRow({
    status: "running", brand_character_record_id: null,
    steps: { ...pendingAll, character: step("completed"), trend: step("waiting_async") },
  } as Partial<WorkRequestRow>);
  assert.equal(characterPending(answered), false);
  assert.equal(chatAskShown(answered as CharacterRow), false);
  assert.equal(stepRows(answered)[0].chip.label, "완료");
  const stopped = waitingRow({ status: "rejected" });
  assert.equal(characterPending(stopped), false);
});

test("기획이 시작된 뒤 바꾸면 '기획서는 예전 캐릭터 기준이에요' (기획 전이면 없음)", () => {
  assert.equal(PLAN_OUTDATED_LINE, "기획서는 예전 캐릭터 기준이에요");
  const steps = {
    ...pendingAll, character: step("completed"), trend: step("completed"), insight: step("completed"),
    plan: step("completed", { started_at: "2026-11-24T01:05:00Z" }), confirm: step("waiting_human", { reason_code: "human_check" }),
  };
  const base = { status: "waiting", plan: PLAN, steps } as CharacterRow;
  assert.equal(planOutdated({ ...base, brand_character_record_id: "CR-1027eb75aaaa", brand_character_at: "2026-11-24T01:10:00Z" }), true);
  assert.equal(planOutdated({ ...base, brand_character_record_id: null, brand_character_at: "2026-11-24T01:01:00Z" }), false);
  assert.equal(planOutdated({ ...base, brand_character_record_id: null }), false); // 시각이 없으면(옛 기록) 띄우지 않는다
  const notYet = { ...base, steps: { ...steps, plan: step("pending") } } as CharacterRow;
  assert.equal(planOutdated({ ...notYet, brand_character_at: "2026-11-24T01:10:00Z" }), false);
});

test("오피스 자리 불: 캐릭터 답 대기는 사람 확인 대기와 같게 총괄 자리 waiting", () => {
  const desks = requestDesks([waitingRow()]);
  assert.deepEqual(desks, [{ desk: "orchestrator", state: "waiting", bubble: null, skill: "brand_character" }]);
});

test("옛 기록(character 단계 없는 홍보 영상)은 지금 그대로: 답 대기로 보지 않는다", () => {
  const old = waitingRow({ plan: PLAN.slice(1).map((p, i) => (i === 0 ? { ...p, after: [] } : p)), steps: { ...pendingAll, trend: step("waiting_async") }, status: "running" });
  assert.equal(characterPending(old), false);
  assert.equal(stepRows(old)[0].label, "트렌드 분석 보고서");
});
