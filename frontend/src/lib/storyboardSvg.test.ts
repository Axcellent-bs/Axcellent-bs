// node --test로 돈다 (npm test). SVG 콘티 보기 · 영상 요약 · 다시 그리기 확인 단계
import assert from "node:assert/strict";
import { test } from "node:test";
import { isSvgBoard, redrawNext, svgCell, svgDataUri } from "./storyboardSvg.ts";
import { videoSummaryLine } from "./creativeVideo.ts";
import type { StoryboardShot } from "./storyboard.ts";

const shot = (over: Partial<StoryboardShot> = {}): StoryboardShot => ({
  no: 1, scene_ko: "놀란 표정 클로즈업", shot_size: "close_up", shot_size_ko: "클로즈업", angle: "eye_level", angle_ko: "눈높이",
  composition_ko: "", has_character: true, prompt_en: "", ...over,
});
const decode = (uri: string) => {
  const prefix = "data:image/svg+xml;base64,";
  assert.ok(uri.startsWith(prefix));
  const bin = atob(uri.slice(prefix.length));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
};

test("svg 칸은 data URI(img src)로만 나온다", () => {
  const c = svgCell(shot({ svg: "<svg xmlns='http://www.w3.org/2000/svg'/>", start_s: 0, end_s: 2, caption_ko: "훅" }));
  assert.ok(c.src?.startsWith("data:image/svg+xml;base64,"));
  assert.equal(c.time, "0~2초");
  assert.equal(c.caption, "훅");
});

test("<script>가 든 svg도 글 그대로 base64에만 담긴다 (src 밖으로 새지 않음)", () => {
  const evil = "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>";
  const c = svgCell(shot({ svg: evil }));
  assert.ok(c.src && !c.src.includes("<script"));
  assert.match(c.src, /^data:image\/svg\+xml;base64,[A-Za-z0-9+/=]+$/);
  assert.equal(decode(c.src), evil);
});

test("한글 캡션 svg는 base64를 풀면 그대로 돌아온다", () => {
  const svg = "<svg xmlns='http://www.w3.org/2000/svg'><text>놀란 표정 클로즈업 · 출근길 지하철 🙂</text></svg>";
  assert.equal(decode(svgDataUri(svg)), svg);
  // 아주 긴 글도
  const big = `<svg>${"가나다".repeat(20000)}</svg>`;
  assert.equal(decode(svgDataUri(big)), big);
});

test("svg가 없거나 빈 칸은 src가 null (구도 그림 칸), 캡션은 장면 글로", () => {
  for (const svg of [undefined, null, "", "   "]) {
    const c = svgCell(shot({ svg }));
    assert.equal(c.src, null);
    assert.equal(c.caption, "놀란 표정 클로즈업");
    assert.equal(c.time, null);
  }
});

test("새 필드가 없으면 지금 화면: board_source 없음·higgsfield·layout은 SVG 보기가 아니다", () => {
  assert.equal(isSvgBoard({}), false);
  assert.equal(isSvgBoard({ board_source: "higgsfield" }), false);
  assert.equal(isSvgBoard({ board_source: "layout" }), false);
  assert.equal(isSvgBoard({ board_source: "svg" }), true);
});

test("영상 요약: 전부 있으면 한 줄", () => {
  const s = { reference: "character_original", model: "Kling 4.0 Flash", seconds: 15, seconds_clamped_from: 20, aspect: "9:16", credits_estimate: 22.5 };
  const want = "참조: 가상 인물 원본 · Kling 4.0 Flash · 15초(20초에서 줄임) · 9:16 · 예상 약 22.5";
  for (const st of ["awaiting_approval", "running", "done"]) assert.equal(videoSummaryLine(st, s), want);
  assert.equal(videoSummaryLine("done", { ...s, reference: "storyboard_image" }), want.replace("가상 인물 원본", "스토리보드 그림"));
});

test("영상 요약: 없는 부분은 빼고, 줄이지 않았으면 안내 없음", () => {
  assert.equal(videoSummaryLine("done", { model: "M", seconds: 10, seconds_clamped_from: 10, aspect: "16:9" }), "M · 10초 · 16:9");
  assert.equal(videoSummaryLine("done", { credits_estimate: 15 }), "예상 약 15");
});

test("영상 요약: summary가 없거나 비었거나 다른 상태면 숨김", () => {
  assert.equal(videoSummaryLine("done", undefined), null);
  assert.equal(videoSummaryLine("done", null), null);
  assert.equal(videoSummaryLine("done", {}), null);
  assert.equal(videoSummaryLine(undefined, { model: "M" }), null);
  assert.equal(videoSummaryLine("failed", { model: "M" }), null);
  assert.equal(videoSummaryLine("rejected", { model: "M" }), null);
});

test("다시 그리기: 누르면 확인 단계, 만들기 전에는 요청 단계로 안 간다", () => {
  assert.equal(redrawNext("idle", "ok"), "idle"); // 확인 없이 바로 만들 수 없다
  assert.equal(redrawNext("idle", "click"), "confirm");
  assert.equal(redrawNext("confirm", "ok"), "running");
  assert.equal(redrawNext("confirm", "cancel"), "idle");
  assert.equal(redrawNext("running", "click"), "running");
  assert.equal(redrawNext("running", "done"), "idle");
});
