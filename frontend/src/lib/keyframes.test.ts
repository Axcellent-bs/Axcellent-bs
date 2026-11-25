// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canRedraw, frameTime, framesOf, keyframePart, keyframeRedrawPath, keyframesDone, keyframesStatusText,
  layoutSrc, redrawConfirmText, sceneActions,
} from "./keyframes.ts";
import { videoSummaryLine } from "./creativeVideo.ts";

const f = (index: number, status = "done", extra = {}) => ({ index, at_s: index * 5, status, ...extra });

test("프레임이 2장 미만이거나 없으면 지금 화면(빈 목록)", () => {
  assert.deepEqual(framesOf(null), []);
  assert.deepEqual(framesOf({ frames: [f(0)] }), []);
  assert.equal(framesOf({ frames: [f(0), f(1)] }).length, 2);
  assert.equal(keyframesStatusText({}), null);
});

test("시각은 기획 시각 × 배속, 구도는 data URI로만", () => {
  assert.equal(frameTime(3, 1.2), "3.6초");
  assert.equal(frameTime(5, null), "5초");
  assert.equal(frameTime(null, 1.5), "0초");
  const src = layoutSrc({ index: 0, svg: '<svg viewBox="0 0 90 160"><rect width="9" height="9"/></svg>' });
  assert.ok(src?.startsWith("data:image/svg+xml;base64,"));
  assert.equal(layoutSrc({ index: 0, svg: "" }), null); // 구도 없이 프롬프트만으로 그린 장
});

test("다시 그리기: 경로·확인 문장·보일 때", () => {
  assert.equal(keyframeRedrawPath("CR-0123456789ab", 2), "/api/dashboard/marketing/creative-records/CR-0123456789ab/keyframes/2/redraw");
  assert.equal(keyframePart(3), "keyframe_3");
  assert.equal(redrawConfirmText(1.5), "그림 크레딧 약 1.5를 써요");
  assert.equal(redrawConfirmText(null), "그림 크레딧 1장을 써요");
  assert.equal(canRedraw(f(0, "failed"), null), true);
  assert.equal(canRedraw(f(0, "running"), null), false);
  assert.equal(canRedraw(f(0, "done"), "awaiting_approval"), true);
  assert.equal(canRedraw(f(0, "done"), "running"), false);
  assert.equal(canRedraw(f(0, "done"), "done"), false);
});

test("상태 글과 모두 그렸는지", () => {
  assert.equal(keyframesStatusText({ frames: [f(0), f(1)] }), "키프레임 2장 완료");
  assert.equal(keyframesStatusText({ frames: [f(0), f(1, "running")] }), "키프레임 그리는 중 (1/2)");
  assert.equal(keyframesStatusText({ frames: [f(0), f(1, "failed")] }), "키프레임 1장 다시 그려야 해요");
  assert.equal(keyframesDone({ frames: [f(0), f(1)] }), true);
  assert.equal(keyframesDone({ frames: [f(0), f(1, "blocked")] }), false);
});

test("장면 동작 한 줄씩 (시각 × 배속), 동작 없는 장면은 뺀다", () => {
  const scenes = [
    { n: 1, start_s: 0, end_s: 5, action_en: "walks forward", camera_en: "slow push-in" },
    { n: 2, start_s: 5, end_s: 10, action_en: "", visual: "" },
    { n: 3, start_s: 10, end_s: 15, action_en: "sits down" },
  ];
  assert.deepEqual(sceneActions(scenes, 1.2), ["0-6초 · walks forward · slow push-in", "12-18초 · sits down"]);
  assert.deepEqual(sceneActions(null), []);
});

test("영상 요약 줄: 참조가 키프레임이면 장 수·그림 크레딧", () => {
  const line = videoSummaryLine("awaiting_approval", {
    reference: "keyframes", keyframes: 4, keyframe_credits: 6, model: "kling4_0", seconds: 18, speed: 1.2, generated_seconds: 18, aspect: "9:16", credits_estimate: 45,
  });
  assert.equal(line, "참조: 키프레임 4장(그림 약 6) · kling4_0 · 18초 만들어 1.2배속 → 15초 · 9:16 · 예상 약 45");
});
