// node --test로 돈다 (npm test). 대화방 의뢰 진행 카드의 회사 캐릭터 질문 (11/24, wr_6eace07d39가 질문 없이 진행된 문제).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHAT_NONE_LINE, UNANSWERED_NOTICE, chatAskShown, chatCharacterLine, unansweredNotice, type CharacterRow,
} from "./requestCharacter.ts";
import type { StepStatus, WorkRequestStep } from "./workRequests.ts";

const step = (status: StepStatus): WorkRequestStep => ({
  status, attempts: 1, grade: null, note: "", reason_code: null, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null,
});
const PLAN = [
  { step_id: "trend", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: [] },
  { step_id: "confirm", dept: null, skill: "human_check", label: "사람 확인", after: ["trend"] },
  { step_id: "prepare", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 초안 준비", after: ["confirm"] },
  { step_id: "make", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["prepare"] },
];
const promo = (extra: Partial<CharacterRow> = {}, prepare: StepStatus = "pending"): CharacterRow => ({
  status: "waiting", task: "promo_video", plan: PLAN,
  steps: { trend: step("completed"), confirm: step("waiting_human"), prepare: step(prepare), make: step("pending") },
  ...extra,
});

test("질문 보이기: 홍보 영상 의뢰 · 영상 준비 pending · 아직 정하지 않음일 때만", () => {
  assert.equal(chatAskShown(promo()), true);
  assert.equal(chatAskShown(promo({ task: null })), true); // 종류가 비면 영상 준비 단계로 본다
  assert.equal(chatAskShown(promo({ task: "trend_report" })), false); // 다른 의뢰 종류
  assert.equal(chatAskShown(promo({ plan: PLAN.filter((p) => p.skill !== "prepare_video_draft_from_plan") })), false);
  assert.equal(chatAskShown(promo({}, "running")), false); // 영상 준비가 시작됐다
  assert.equal(chatAskShown(promo({ brand_character_record_id: "CR-0123456789ab" })), false); // 이미 정함
  assert.equal(chatAskShown(promo({ brand_character_record_id: null })), false); // 캐릭터 없이로 답함
});

test("한 줄 문구: 고른 캐릭터는 이름(CR-…), 없음은 '캐릭터 없이 진행', 영상 준비가 시작되면 바꾸기 없음", () => {
  assert.equal(chatCharacterLine(promo()), null); // 아직 답하지 않음 → 질문
  assert.deepEqual(chatCharacterLine(promo({ brand_character_record_id: "CR-0123456789ab" }), "책방 고양이"), {
    text: "회사 캐릭터: 책방 고양이(CR-0123456789ab)", canChange: true,
  });
  assert.equal(chatCharacterLine(promo({ brand_character_record_id: "CR-0123456789ab" }))?.text, "회사 캐릭터: CR-0123456789ab");
  assert.deepEqual(chatCharacterLine(promo({ brand_character_record_id: null })), { text: CHAT_NONE_LINE, canChange: true });
  assert.deepEqual(chatCharacterLine(promo({ brand_character_record_id: null }, "completed")), { text: "캐릭터 없이 진행", canChange: false });
  assert.equal(chatCharacterLine(promo({ task: "trend_report", brand_character_record_id: null })), null);
});

test("진행 버튼 위 안내: 다음 단계가 영상 준비인 사람 확인 단계에서, 답하지 않았을 때만", () => {
  assert.equal(unansweredNotice(promo(), "confirm"), UNANSWERED_NOTICE);
  assert.equal(UNANSWERED_NOTICE, "회사 캐릭터를 정하지 않았어요. 이대로 진행하면 캐릭터 없이 만들어요.");
  assert.equal(unansweredNotice(promo(), "trend"), null); // 다음이 영상 준비가 아니다
  assert.equal(unansweredNotice(promo({ brand_character_record_id: null }), "confirm"), null); // 답했다
  assert.equal(unansweredNotice(promo({ brand_character_record_id: "CR-0123456789ab" }), "confirm"), null);
  assert.equal(unansweredNotice(promo({ task: "trend_report" }), "confirm"), null);
});
