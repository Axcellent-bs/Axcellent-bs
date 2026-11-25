// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { approvalChipKo, leftKo, secondsLeft, timedOut } from "./videoApprovalTime.ts";
import { videoRejected, VIDEO_REJECTED } from "./requestCharacter.ts";

const NOW = Date.parse("2026-11-26T10:00:00Z");

test("마감까지 남은 초 — 모르거나 날짜가 아니면 null", () => {
  assert.equal(secondsLeft(null, NOW), null);
  assert.equal(secondsLeft("not-a-date", NOW), null);
  assert.equal(secondsLeft("2026-11-26T10:52:00Z", NOW), 52 * 60);
  assert.equal(secondsLeft("2026-11-26T09:59:00Z", NOW), -60);
});

test("남은 시간 문구: 분 · 시간 분 · 1분 미만 · 기한 지남", () => {
  assert.equal(leftKo(52 * 60), "52분 남음");
  assert.equal(leftKo(52 * 60 + 59), "52분 남음"); // 남은 분은 내림(더 남았다고 말하지 않는다)
  assert.equal(leftKo((23 * 60 + 52) * 60), "23시간 52분 남음");
  assert.equal(leftKo(3600), "1시간 남음");
  assert.equal(leftKo(30), "1분 미만 남음");
  assert.equal(leftKo(0), "기한 지남");
  assert.equal(leftKo(null), null);
});

test("승인 대기 칩: 마감을 알면 남은 시간, 모르면 '승인 대기'만", () => {
  assert.equal(approvalChipKo(secondsLeft("2026-11-26T10:52:00Z", NOW)), "승인 대기 · 52분 남음");
  assert.equal(approvalChipKo(null), "승인 대기");
  assert.equal(approvalChipKo(-5), "승인 대기 · 기한 지남");
});

test("기한 지나 취소됨은 review_timeout만 — 사람 거절·대기·완료는 아니다", () => {
  assert.equal(timedOut({ status: "rejected", reason: "review_timeout" }), true);
  assert.equal(timedOut({ status: "rejected", reason: "rejected" }), false);
  assert.equal(timedOut({ status: "rejected" }), false); // 옛 기록(이유 없음)은 지금처럼
  assert.equal(timedOut({ status: "awaiting_approval", reason: "review_timeout" }), false);
  assert.equal(timedOut(null), false);
});

test("의뢰 카드: 영상 단계가 video_rejected로 닫혔을 때만 다시 요청 칸을 찾는다", () => {
  const plan = [{ step_id: "s7", dept: "marketing", skill: "generate_media", label: "영상", after: [] }];
  const step = (status: string, reason_code: string | null) => ({
    status, attempts: 0, grade: null, note: "", reason_code, result_ref: null, review_id: null,
    handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null,
  });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const row = (s: ReturnType<typeof step>) => ({ plan, steps: { s7: s } }) as any;
  assert.equal(videoRejected(row(step("rejected_by_human", VIDEO_REJECTED))), true);
  assert.equal(videoRejected(row(step("held", "video_approval"))), false);
  assert.equal(videoRejected(row(step("failed", VIDEO_REJECTED))), false);
});
