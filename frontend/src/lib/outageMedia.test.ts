import assert from "node:assert/strict";
import { test } from "node:test";
import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";
import { OUTAGE_CONFIRM_NOTES, outageBlockKo, outageHttpErrorKo, outageMediaResultKo, outagePublishResultKo, shouldRequestPublish } from "./outageMedia.ts";

test("진행 불가 이유는 한국어로 안내한다", () => {
  for (const code of ["already_merged", "publish_history", "check_failed", "video_not_supported"]) {
    assert.match(outageBlockKo(code), /[가-힣]/);
    assert.ok(!outageBlockKo(code).includes(code));
  }
  assert.match(outageBlockKo("budget_exceeded"), /상한/); // 비용 쪽 이유는 기존 문구를 쓴다
});

test("확인 단계 안내에 두 번째 사람 승인과 만료 시 비용만 남는다는 말이 있다", () => {
  const text = OUTAGE_CONFIRM_NOTES.join("\n");
  assert.match(text, /게시는 사람 승인이 한 번 더 필요/);
  assert.match(text, /10분이 지나면.*이미지 비용만 남/);
});

test("이미지가 준비된 경우에만 게시 요청을 이어 보낸다", () => {
  assert.equal(shouldRequestPublish({ status: "completed", outcome: null, output: { stage: "draft_ready" } }), true);
  assert.equal(shouldRequestPublish({ status: "completed", outcome: "already_prepared", output: { stage: "draft_ready" } }), true);
  assert.equal(shouldRequestPublish({ status: "completed", outcome: "rejected_by_check", output: { stage: "check_rejected" } }), false);
  assert.equal(shouldRequestPublish({ status: "input-required", outcome: "held", output: { stage: "generate_media" } }), false);
  assert.equal(shouldRequestPublish({ status: "failed", outcome: "budget_exceeded", output: { stage: "generate_media" } }), false);
});

test("이미지 만들기 결과 문구", () => {
  assert.equal(outageMediaResultKo({ status: "completed", outcome: null, output: { stage: "draft_ready" } }).kind, "ready");
  assert.equal(outageMediaResultKo({ status: "completed", outcome: "rejected_by_check", output: { stage: "check_rejected" } }).kind, "check_rejected");
  assert.equal(outageMediaResultKo({ status: "input-required", outcome: "held", output: { review_id: "r" } }).kind, "held");
  const merged = outageMediaResultKo({ status: "failed", outcome: "already_merged", output: { stage: "preflight" } });
  assert.equal(merged.kind, "failed");
  assert.match(merged.text, /병합/);
  const over = outageMediaResultKo({ status: "failed", outcome: "budget_exceeded", output: { stage: "generate_media" } });
  assert.match(over.text, /상한/);
});

test("게시 요청 결과는 아직 게시되지 않았음을 알린다", () => {
  const ok = outagePublishResultKo({ status: "completed", outcome: "requested", output: null });
  assert.equal(ok.kind, "requested");
  assert.match(ok.text, /아직 게시되지 않았습니다/);
  assert.equal(outagePublishResultKo({ status: "failed", outcome: "not_prepared", output: null }).kind, "failed");
  assert.equal(outagePublishResultKo({ status: "failed", outcome: "adapter_unavailable", output: null }).kind, "failed");
});

test("연결 오류 문구는 경로·내부 주소 없이 공용 안내를 쓴다", () => {
  assert.equal(outageHttpErrorKo(403, NGINX_BLOCKED_CODE), NGINX_BLOCKED_MESSAGE);
  for (const status of [400, 401, 403, 404, 502, 504, 500, undefined]) {
    const text = outageHttpErrorKo(status);
    assert.ok(!/\/api\/|https?:|localhost|nginx/i.test(text), text);
    assert.match(text, /[가-힣]/);
  }
});
