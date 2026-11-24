// node --test로 돈다 (npm test). 만들기 설정: API 없으면 숨김, 읽기 전용, 모델 바꾸면 범위, 저장 본문, 오류 문구.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "./apiError.ts";
import {
  FORBIDDEN_NOTE, aspectOptions, changeVideoModel, defaultHint, loadRenderSettings, parseRenderSettings, saveBody, saveErrorKo,
  secondsOptions, startValues, videoModelOf, type RenderSettingsState,
} from "./renderSettings.ts";

const RAW = {
  settings: null,
  defaults: { image_model: "nb2", video_model: "kling", image_credits_ok: false, video_seconds: "plan", aspect: "9:16" },
  models: {
    image: [{ id: "nb2", name: "Nano Banana 2" }],
    video: [
      { id: "kling", name: "Kling 4.0 Flash", min_s: 3, max_s: 20, aspects: ["9:16", "1:1", "16:9"] },
      { id: "short", name: "Short", min_s: 5, max_s: 8, aspects: ["9:16"] },
    ],
  },
  can_edit: true,
};
const state = (): RenderSettingsState => {
  const s = parseRenderSettings(RAW);
  assert.ok(s);
  return s;
};

test("API가 없으면(404·실패) 칸 전체 숨김 = null", async () => {
  assert.equal(await loadRenderSettings(() => Promise.reject(new ApiError("요청 실패 (404)", 404))), null);
  assert.equal(await loadRenderSettings(() => Promise.reject(new Error("network"))), null);
});

test("응답이 이상하면 숨김", async () => {
  assert.equal(await loadRenderSettings(() => Promise.resolve(undefined)), null);
  assert.equal(await loadRenderSettings(() => Promise.resolve({})), null);
  assert.equal(await loadRenderSettings(() => Promise.resolve("<html>")), null);
  assert.equal(await loadRenderSettings(() => Promise.resolve({ ...RAW, can_edit: "yes" })), null);
  assert.equal(await loadRenderSettings(() => Promise.resolve({ ...RAW, models: { image: [] } })), null);
  assert.equal(await loadRenderSettings(() => Promise.resolve({ ...RAW, defaults: { ...RAW.defaults, aspect: "4:3" } })), null);
});

test("정상 응답은 읽는다", async () => {
  const s = await loadRenderSettings(() => Promise.resolve(RAW));
  assert.equal(s?.can_edit, true);
  assert.equal(s?.models.video.length, 2);
});

test("읽기 전용 값(can_edit false)을 그대로 가져온다", () => {
  const s = parseRenderSettings({ ...RAW, can_edit: false });
  assert.equal(s?.can_edit, false);
});

test("영상 모델에 따라 길이·비율 선택지가 바뀐다", () => {
  const s = state();
  assert.deepEqual(secondsOptions(videoModelOf(s, "kling")).slice(0, 2), [3, 4]);
  assert.equal(secondsOptions(videoModelOf(s, "kling")).length, 18);
  assert.deepEqual(secondsOptions(videoModelOf(s, "short")), [5, 6, 7, 8]);
  assert.deepEqual(aspectOptions(videoModelOf(s, "short")), ["9:16"]);
  assert.deepEqual(secondsOptions(videoModelOf(s, "unknown")), []);
  assert.deepEqual(aspectOptions(videoModelOf(s, "unknown")), ["9:16", "1:1", "16:9"]);
});

test("모델을 바꿔 범위를 벗어나면 기획서 따르기로", () => {
  const s = state();
  const cur = { ...startValues(s), video_model: "kling", video_seconds: 15 as const, aspect: "1:1" as const };
  const n = changeVideoModel(s, cur, "short");
  assert.equal(n.video_seconds, "plan");
  assert.equal(n.aspect, "plan");
  const keep = changeVideoModel(s, { ...cur, video_seconds: 6, aspect: "9:16" }, "short");
  assert.equal(keep.video_seconds, 6);
  assert.equal(keep.aspect, "9:16");
  assert.equal(changeVideoModel(s, { ...cur, video_seconds: null, aspect: null }, "short").video_seconds, null);
});

test("저장 본문은 5개 키 모두, 비운 칸은 null", () => {
  const body = saveBody({ image_model: "", video_model: "kling", image_credits_ok: false, video_seconds: 10, aspect: "plan" });
  assert.deepEqual(body, { image_model: null, video_model: "kling", image_credits_ok: false, video_seconds: 10, aspect: "plan" });
  assert.deepEqual(Object.keys(saveBody(startValues(state()))).sort(), ["aspect", "image_credits_ok", "image_model", "video_model", "video_seconds"]);
});

test("비운 칸 옆에 서버 기본값을 회색으로", () => {
  const s = state();
  assert.equal(defaultHint(s, "image_model"), "Nano Banana 2");
  assert.equal(defaultHint(s, "image_credits_ok"), "쓰지 않기");
  assert.equal(defaultHint(s, "video_seconds"), "기획서 따르기");
  assert.equal(defaultHint(s, "aspect"), "9:16");
});

test("저장 오류: 422는 서버 문장 그대로, 403은 고정 문구", () => {
  assert.equal(saveErrorKo(new ApiError("영상 길이는 3~20초예요", 422)), "영상 길이는 3~20초예요");
  assert.equal(saveErrorKo(new ApiError("forbidden", 403)), FORBIDDEN_NOTE);
  assert.equal(FORBIDDEN_NOTE, "회사 관리자만 바꿀 수 있어요");
});
