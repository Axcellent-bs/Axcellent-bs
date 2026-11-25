// node --test로 돈다 (npm test). 캐릭터를 영상 요청 전까지 바꾸기 (11/26, 총괄 `_reprepare`와 같은 규칙).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  REPREPARE_CONFIRM, VIDEO_LOCKED_LINE, askMode, characterOpen, characterStage, chatAskShown, needsReprepare,
  type CharacterRow,
} from "./requestCharacter.ts";
import type { StepStatus, WorkRequestStep } from "./workRequests.ts";

const step = (status: StepStatus): WorkRequestStep => ({
  status, attempts: 1, grade: null, note: "", reason_code: null, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null,
});
const PLAN = [
  { step_id: "confirm", dept: null, skill: "human_check", label: "사람 확인", after: [] },
  { step_id: "prepare", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 초안 준비", after: ["confirm"] },
  { step_id: "make", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["prepare"] },
];
const row = (prepare: StepStatus, make: StepStatus, extra: Partial<CharacterRow> = {}): CharacterRow => ({
  status: "running", task: "promo_video", plan: PLAN, brand_character_record_id: "CR-0123456789ab",
  steps: { confirm: step("completed"), prepare: step(prepare), make: step(make) }, ...extra,
});

test("prepare 전: 그냥 바뀐다(다시 준비 안 물음)", () => {
  const r = row("pending", "pending");
  assert.equal(characterStage(r), "before_prepare");
  assert.equal(characterOpen(r), true);
  assert.equal(needsReprepare(r), false);
});

test("prepare 뒤·영상 요청 전: 바꾸면 콘티·키프레임을 다시 준비한다고 한 번 더 묻는다", () => {
  for (const make of ["pending", "running", "waiting_async"] as StepStatus[]) {
    const r = row("completed", make);
    assert.equal(characterStage(r), "after_prepare", make);
    assert.equal(characterOpen(r), true);
    assert.equal(needsReprepare(r), true);
    assert.equal(askMode(r, []), "chosen"); // [바꾸기]가 보인다
  }
  assert.equal(REPREPARE_CONFIRM, "콘티·키프레임을 새 캐릭터로 다시 준비해요(그림 크레딧 다시 씀)");
  assert.equal(chatAskShown(row("completed", "pending", { brand_character_record_id: undefined })), false); // 새 질문은 안 띄운다
});

test("영상 요청 뒤(승인 대기·끝남·거절)·준비 중·닫힌 의뢰는 막는다", () => {
  for (const make of ["held", "completed", "rejected_by_human"] as StepStatus[]) {
    const r = row("completed", make);
    assert.equal(characterStage(r), "video_requested", make);
    assert.equal(characterOpen(r), false);
    assert.equal(askMode(r, []), "closed");
  }
  assert.equal(VIDEO_LOCKED_LINE, "영상 요청 뒤라 바꿀 수 없어요");
  assert.equal(characterStage(row("running", "pending")), "preparing");
  assert.equal(characterOpen(row("running", "pending")), false);
  assert.equal(characterStage(row("completed", "pending", { status: "done" })), "closed");
  assert.equal(characterStage(row("completed", "pending", { stopped_at: "2026-11-26T00:00:00Z" })), "closed");
});
