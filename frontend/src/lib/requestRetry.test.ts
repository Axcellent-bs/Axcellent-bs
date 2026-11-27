// 의뢰 단계 다시 시도 버튼 문구·판정 (11/27). node --test src/lib/requestRetry.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { FULL_RERUN_LABEL, RETRY_LABEL, canRetryStep, retryButton, retryCountLine, retryError, retryLimited } from "./requestRetry.ts";

test("실패·영상 거절(기한 지남)·막힘만 다시 시도 (총괄 retryable과 같은 규칙)", () => {
  assert.equal(canRetryStep({ status: "failed", reason_code: null }), true);
  assert.equal(canRetryStep({ status: "rejected_by_human", reason_code: "video_rejected" }), true);
  assert.equal(canRetryStep({ status: "skipped", reason_code: "no_insight" }), true);
  assert.equal(canRetryStep({ status: "rejected_by_human", reason_code: "human_check" }), false); // 사람이 제작안을 멈춤
  assert.equal(canRetryStep({ status: "skipped", reason_code: null }), false); // 앞 단계 때문에 건너뜀
  assert.equal(canRetryStep({ status: "completed", reason_code: null }), false);
  assert.equal(canRetryStep({ status: "waiting_async", reason_code: null }), false);
  assert.equal(canRetryStep(undefined), false);
});

test("버튼 글: 영상은 크레딧 예상 + 승인, 준비는 그림 크레딧, 그 밖은 그대로", () => {
  assert.equal(retryButton("generate_media", 27), "이 단계부터 다시 · 영상 약 27 크레딧 · 승인 필요");
  assert.equal(retryButton("generate_media", null), "이 단계부터 다시 · 영상 승인 필요");
  assert.equal(retryButton("prepare_video_draft_from_plan"), "이 단계부터 다시 · 스토리보드 그림 없으면 약 1.5 크레딧");
  assert.equal(retryButton("trend_report_create"), RETRY_LABEL);
  assert.equal(retryButton("receive_insight"), RETRY_LABEL);
  assert.equal(FULL_RERUN_LABEL, "처음부터 다시(새 제작안)");
});

test("다시 시도 n/3 · 상한", () => {
  assert.equal(retryCountLine({ status: "failed", reason_code: null }), null);
  assert.equal(retryCountLine({ status: "failed", reason_code: null, retry_count: 2 }), "다시 시도 2/3");
  assert.equal(retryLimited({ status: "failed", reason_code: null, retry_count: 2 }), false);
  assert.equal(retryLimited({ status: "failed", reason_code: null, retry_count: 3 }), true);
});

test("실패 문구: 422는 서버 문장, 409는 진행 중", () => {
  const e422 = Object.assign(new Error("이 단계는 다시 시도를 3번까지 했어요"), { status: 422 });
  assert.equal(retryError(e422).text, "이 단계는 다시 시도를 3번까지 했어요");
  assert.match(retryError(Object.assign(new Error("x"), { status: 409 })).text, /진행 중/);
  assert.match(retryError(Object.assign(new Error("x"), { status: 404 })).text, /찾지 못했어요/);
});
