// node --test로 돈다 (npm test). 홍보 영상 의뢰의 회사 캐릭터 질문·대화방 시트/스토리보드 버튼 계산 (11/23).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHARACTER_NOTE, RIGHTS_LABEL, askMode, canUpload, characterOpen, creativeButtons, creativeRecordOf, sceneCells, savedLine,
  videoApproval, type CharacterRow, type SavedCharacter,
} from "./requestCharacter.ts";
import type { StepStatus, WorkRequestStep } from "./workRequests.ts";

const step = (status: StepStatus, extra: Partial<WorkRequestStep> = {}): WorkRequestStep => ({
  status, attempts: 1, grade: null, note: "", reason_code: null, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null, ...extra,
});
const PLAN = [
  { step_id: "trend", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: [] },
  { step_id: "confirm", dept: null, skill: "human_check", label: "사람 확인", after: ["trend"] },
  { step_id: "prepare", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 초안 준비", after: ["confirm"] },
  { step_id: "make", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["prepare"] },
];
const promo = (steps: Record<string, WorkRequestStep>, status: CharacterRow["status"] = "waiting", extra: Partial<CharacterRow> = {}): CharacterRow => ({
  status, plan: PLAN, steps: { trend: step("completed"), confirm: step("waiting_human"), prepare: step("pending"), make: step("pending"), ...steps }, ...extra,
});
const SAVED: SavedCharacter[] = [{ record_id: "CR-0123456789ab", name: "책방 고양이", saved_at: "2026-11-20T03:00:00+00:00", is_default: true }];

test("질문 카드 세 경우: 저장된 캐릭터가 있으면 먼저 보이고, 없으면 있어요·없어요, 고르면 고른 값", () => {
  assert.equal(askMode(promo({}), SAVED), "saved"); // [이 캐릭터로 만들기][다른 이미지 올리기][캐릭터 없이]
  assert.equal(askMode(promo({}), []), "ask"); // [있어요][없어요]
  assert.equal(askMode(promo({}), null), "ask"); // 목록을 아직 못 읽었으면 묻기부터
  assert.equal(askMode(promo({}, "waiting", { brand_character_record_id: "CR-0123456789ab" }), SAVED), "chosen");
  assert.equal(askMode(promo({}, "waiting", { brand_character_record_id: null }), SAVED), "none"); // 캐릭터 없이
  assert.equal(CHARACTER_NOTE, "캐릭터를 쓰면 영상을 만들기 전에 사람 승인을 한 번 받아요.");
});

test("영상 준비가 시작되면 더 묻지 않는다 · 홍보 영상이 아니면 카드가 없다", () => {
  const started = promo({ confirm: step("completed"), prepare: step("running") }, "running");
  assert.equal(characterOpen(started), false);
  assert.equal(askMode(started, SAVED), "closed");
  assert.equal(askMode(promo({}, "rejected"), SAVED), "closed"); // 끝난 의뢰
  assert.equal(askMode(promo({}, "waiting", { stopped_at: "2026-11-23T00:00:00Z" }), SAVED), "closed"); // 삭제로 멈춘 의뢰
  const other: CharacterRow = { status: "waiting", plan: PLAN.slice(0, 1), steps: { trend: step("running") } };
  assert.equal(askMode(other, SAVED), "hidden");
});

test("권리 체크를 해야 올리기 버튼이 켜진다", () => {
  assert.equal(RIGHTS_LABEL, "우리 회사가 쓸 권리가 있는 그림이에요");
  assert.equal(canUpload(true, false), false);
  assert.equal(canUpload(false, true), false);
  assert.equal(canUpload(true, true), true);
  assert.equal(canUpload(true, true, true), false); // 올리는 중
  assert.match(savedLine(SAVED[0]), /^이미지 시트 CR-0123456789ab · 11\/20 저장$/);
});

test("카드 버튼: 제작 기록이 있으면 [이미지 시트][스토리보드], 없으면 장면 글이 있을 때 [스토리보드]만", () => {
  const plan = { scenes: [{ n: 1, startS: 0, endS: 5, visual: "책방 문을 여는 고양이", caption: "어서 와", prompt: "p" }] };
  const withRecord = promo({ prepare: step("completed", { result_ref: "CR-fedcba987654" }) }, "running");
  assert.equal(creativeRecordOf(withRecord), "CR-fedcba987654");
  assert.deepEqual(creativeButtons(withRecord, plan), { sheet: true, storyboard: true });
  const noRecord = promo({ prepare: step("completed", { result_ref: "spd_123" }) }, "running");
  assert.equal(creativeRecordOf(noRecord), null); // CR 모양이 아니면 쓰지 않는다
  assert.deepEqual(creativeButtons(noRecord, plan), { sheet: false, storyboard: true });
  assert.deepEqual(creativeButtons(noRecord, null), { sheet: false, storyboard: false });
  assert.deepEqual(sceneCells(plan), [{ key: 1, title: "장면 1", text: "책방 문을 여는 고양이", caption: "어서 와" }]);
  const other: CharacterRow = { status: "done", plan: PLAN.slice(0, 1), steps: { trend: step("completed") } };
  assert.deepEqual(creativeButtons(other, plan), { sheet: false, storyboard: false });
});

test("영상 승인 대기: 영상 제작 단계가 held·video_approval일 때만 (다른 승인 대기와 구별)", () => {
  const held = promo({ make: step("held", { reason_code: "video_approval", review_id: "rev_1" }) }, "waiting");
  assert.deepEqual(videoApproval(held), { stepId: "make", reviewId: "rev_1" });
  assert.equal(videoApproval(promo({ make: step("held", { reason_code: "human_input" }) })), null);
  assert.equal(videoApproval(promo({ make: step("waiting_async") }, "running")), null);
});
