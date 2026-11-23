// node --test로 돈다 (npm test). 고객 의뢰 삭제(멈추고 숨김)·의뢰문 수정(고쳐서 다시 접수) 화면 계산 (11/23).
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "./apiError.ts";
import { requestEditError } from "./requestCards.ts";
import { EDIT_NOTE, canRerun, deleteConfirm, editProblem, remainingSteps, type StepStatus, type WorkRequestRow } from "./workRequests.ts";

const step = (status: StepStatus) => ({
  status, attempts: 1, grade: null, note: "", reason_code: null, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null,
});
const row = (status: WorkRequestRow["status"], steps: Record<string, StepStatus>): Pick<WorkRequestRow, "plan" | "steps" | "status"> => ({
  status,
  plan: [
    { step_id: "s1", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: [] },
    { step_id: "s4", dept: null, skill: "human_check", label: "사람 확인", after: ["s1"] },
    { step_id: "s5", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 준비", after: ["s4"] },
    { step_id: "s6", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["s5"] },
  ],
  steps: Object.fromEntries(Object.entries(steps).map(([k, v]) => [k, step(v)])),
});

test("삭제 확인 상자: 남은 단계 이름을 넣은 시안 문구, 끝난 의뢰는 숨기기만", () => {
  const open = row("waiting", { s1: "completed", s4: "waiting_human", s5: "pending", s6: "pending" });
  assert.deepEqual(remainingSteps(open), ["사람 확인", "영상 준비", "영상 제작"]);
  assert.deepEqual(deleteConfirm("홍보 영상 만들기", open), {
    head: "\"홍보 영상 만들기\" 의뢰를 삭제할까요?",
    body: "남은 단계(사람 확인·영상 준비·영상 제작)를 멈추고 목록에서 숨겨요. 이미 만든 보고서·기획서·영상은 각 부서에 남아요.",
  });
  const stuck = row("needs_human", { s1: "failed", s4: "pending", s5: "pending", s6: "pending" });
  assert.deepEqual(remainingSteps(stuck), ["사람 확인", "영상 준비", "영상 제작"]); // 멈춘 의뢰(사람 확인 필요)도 멈춘다
  const done = row("done", { s1: "completed", s4: "completed", s5: "completed", s6: "completed" });
  assert.deepEqual(remainingSteps(done), []);
  assert.equal(deleteConfirm("홍보 영상 만들기", done).body, "목록에서 숨겨요. 이미 만든 보고서·기획서·영상은 각 부서에 남아요.");
});

test("의뢰문 수정: 빈 글·너무 긴 글·같은 글(띄어쓰기만 다름)은 보낼 수 없다", () => {
  const original = "홍보 영상 만들어 주세요";
  assert.equal(editProblem("  ", original), "의뢰문을 적어 주세요");
  assert.equal(editProblem("가".repeat(2001), original), "의뢰문은 2,000자까지예요");
  assert.equal(editProblem(" 홍보  영상 만들어 주세요\n", original), "바뀐 내용이 없어요");
  assert.equal(editProblem("홍보 영상 30초로 만들어 주세요", original), null);
  assert.ok(EDIT_NOTE.startsWith("고친 글로 새 의뢰를 접수하고, 지금 의뢰는 멈추고 목록에서 숨겨요."));
});

test("[일 다시 고르기]: 삭제로 멈춘 의뢰(되돌린 의뢰 포함)에는 숨긴다", () => {
  const stopped = row("rejected", { s1: "completed", s4: "skipped", s5: "skipped", s6: "skipped" });
  assert.equal(canRerun(stopped), true); // 멈추지 않은 거절 의뢰는 그대로 다시 고를 수 있다
  assert.equal(canRerun({ ...stopped, stopped_at: "2026-11-23T10:00:00+00:00" }), false);
  // 숨긴 목록에서 되돌린 의뢰: 숨김은 풀렸어도 stopped_at은 남는다
  assert.equal(canRerun({ ...stopped, stopped_at: "2026-11-23T10:00:00+00:00", hidden_at: null } as WorkRequestRow), false);
  const busy = row("running", { s1: "running", s4: "pending", s5: "pending", s6: "pending" });
  assert.equal(canRerun(busy), false);
});

test("실패 문구: 403·404·409는 결정 실패 문구 그대로, 422·502·503은 서버 문장", () => {
  assert.deepEqual(requestEditError(new ApiError("x", 403)).text, "서버가 막았어요 (403) · 관리자에게 이 경로 허용을 요청해 주세요");
  assert.deepEqual(requestEditError(new ApiError("x", 404)).text, "의뢰나 단계를 찾지 못했어요");
  assert.deepEqual(requestEditError(new ApiError("x", 409)), { text: "이미 다른 사람이 처리했어요", reload: true });
  assert.equal(requestEditError(new ApiError("바뀐 내용이 없어요", 422)).text, "바뀐 내용이 없어요");
  assert.equal(requestEditError(new ApiError("새 의뢰를 접수하지 못했어요. 지금 의뢰는 그대로예요", 502)).text, "새 의뢰를 접수하지 못했어요. 지금 의뢰는 그대로예요");
  assert.equal(requestEditError(new TypeError("Failed to fetch")).text, "처리하지 못했어요");
});

test("의뢰문 수정에서 새 의뢰가 안 생긴 세 경우: 서버 문장을 보이고 수정 칸은 열어 둔다(다시 읽지 않음)", () => {
  const dup = requestEditError(new ApiError("같은 의뢰가 이미 접수돼 있어요 (번호 wr_abc123)", 422));
  assert.deepEqual(dup, { text: "같은 의뢰가 이미 접수돼 있어요 (번호 wr_abc123)", reload: false });
  const limited = requestEditError(new ApiError("오늘 접수할 수 있는 의뢰 수를 넘었어요. 내일 다시 고쳐 주세요", 429));
  assert.deepEqual(limited, { text: "오늘 접수할 수 있는 의뢰 수를 넘었어요. 내일 다시 고쳐 주세요", reload: false });
  const rejected = requestEditError(new ApiError("고친 글은 접수할 수 없는 의뢰예요. 지금 의뢰는 그대로예요", 422));
  assert.equal(rejected.text, "고친 글은 접수할 수 없는 의뢰예요. 지금 의뢰는 그대로예요");
  assert.ok(![dup, limited, rejected].some((x) => x.text.includes("의뢰로 접수되지 않았어요")));
});
