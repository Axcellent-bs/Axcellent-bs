// node --test로 돈다 (npm test). 영상 기술 검사 칩·표·다시 만들기 (11/26 H).
import assert from "node:assert/strict";
import { test } from "node:test";
import { canRegenerate, qcView, regenerateLabel, regenerateNote, videoRequestBody, type VideoQcState } from "./videoQcView.ts";

const qc = (statuses: Record<string, "pass" | "warn" | "error">): VideoQcState => ({
  video_sha: "a".repeat(64), part: "overlay_video",
  checks: Object.entries(statuses).map(([id, status]) => ({ id: id as "black", status, detail_ko: `${id} ${status}`, ranges: [] })),
});

test("칩: 확인 필요 N개 > 확인 못 함 > 모두 통과, 결과 없으면 null", () => {
  assert.deepEqual(qcView(qc({ black: "pass", freeze: "warn", duration: "pass" }))?.chip, { label: "1개 확인 필요", tone: "bad" });
  assert.deepEqual(qcView(qc({ black: "pass", freeze: "pass", duration: "pass" }))?.chip, { label: "모두 통과", tone: "ok" });
  assert.deepEqual(qcView(qc({ black: "error", freeze: "error", duration: "pass" }))?.chip, { label: "2개 확인 못 함", tone: "muted" });
  assert.equal(qcView(null), null);
  assert.equal(qcView({ checks: [] }), null);
});

test("표: 검사 이름·결과·내용 (서버 글 그대로)", () => {
  const v = qcView(qc({ black: "pass", freeze: "warn", duration: "error" }))!;
  assert.deepEqual(v.rows.map((r) => [r.label, r.result, r.detail]), [
    ["검은 화면", "통과", "black pass"], ["멈춘 화면", "확인 필요", "freeze warn"], ["길이", "확인 못 함", "duration error"],
  ]);
  assert.equal(v.needsAction, true);
});

test("다시 만들기: 다 만든 영상 + 확인 필요일 때만, 크레딧은 영상 요약 예상 값", () => {
  const warn = qc({ black: "pass", freeze: "warn", duration: "pass" });
  assert.equal(canRegenerate("done", warn), true);
  assert.equal(canRegenerate("awaiting_approval", warn), false);
  assert.equal(canRegenerate("done", qc({ black: "pass", freeze: "pass", duration: "error" })), false);
  assert.equal(regenerateLabel(27), "영상 다시 만들기 · 사람 결정 · 약 27 크레딧");
  assert.equal(regenerateLabel(null), "영상 다시 만들기 · 사람 결정");
});

test("다시 만들기가 거절·기한 지남·막힘이면 예전 영상을 쓴다는 한 줄", () => {
  assert.equal(regenerateNote(null), null);
  assert.equal(regenerateNote({ status: "rejected", reason: "rejected" }), "다시 만들기가 거절돼 예전 영상을 그대로 써요(게시·썸네일 포함).");
  assert.match(regenerateNote({ status: "rejected", reason: "review_timeout" })!, /기한이 지나 취소돼/);
  assert.match(regenerateNote({ status: "rejected", reason: "blocked" })!, /승인 관문에서 막혀/);
});

test("버튼은 지금 있는 영상 요청 길을 regenerate만 붙여 부른다", () => {
  assert.equal(videoRequestBody(), "{}");
  assert.equal(videoRequestBody({ regenerate: false }), "{}");
  assert.deepEqual(JSON.parse(videoRequestBody({ regenerate: true })), { regenerate: true });
});
