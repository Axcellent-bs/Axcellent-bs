// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { HIGGSFIELD_URL, UPLOAD_MAX_BYTES, VIDEO_PROMPT_MAX, acceptFor, pickDropped, secs, storyboardVideoPrompt, uploadProblem } from "./higgsfieldWeb.ts";

const BASE = "Animate this storyboard as one continuous short film, panel by panel in order, keeping the same characters, outfits and art style.";

test("영상 프롬프트는 마케팅 creative_render.storyboard_video_prompt와 같은 문장이다", () => {
  assert.equal(storyboardVideoPrompt("flat pastel 2D"), `${BASE} flat pastel 2D`);
  assert.equal(storyboardVideoPrompt(null), BASE);
  assert.equal(storyboardVideoPrompt(""), BASE);
  assert.equal(storyboardVideoPrompt(undefined), BASE);
});

test("Higgsfield 주소는 https 첫 화면이다", () => {
  assert.equal(HIGGSFIELD_URL, "https://higgsfield.ai/");
});

test("영상 칸은 MP4만, 그림 칸은 JPG·PNG만 받는다", () => {
  assert.equal(uploadProblem("final_video", { name: "a.mp4", type: "video/mp4", size: 10 }), null);
  assert.equal(uploadProblem("video", { name: "a.mp4", type: "video/mp4", size: 10 }), null);
  assert.equal(uploadProblem("final_video", { name: "a.mov", type: "video/quicktime", size: 10 }), "MP4 영상만 올릴 수 있어요");
  assert.equal(uploadProblem("video", { name: "a.png", type: "image/png", size: 10 }), "MP4 영상만 올릴 수 있어요");
  assert.equal(uploadProblem("board_image", { name: "a.png", type: "image/png", size: 10 }), null);
  assert.equal(uploadProblem("sheet_image", { name: "a.jpg", type: "image/jpeg", size: 10 }), null);
  assert.equal(uploadProblem("sheet_image", { name: "a.mp4", type: "video/mp4", size: 10 }), "JPG·PNG 그림만 올릴 수 있어요");
  assert.equal(acceptFor("final_video"), "video/mp4");
  assert.equal(acceptFor("board_image"), "image/jpeg,image/png");
});

test("브라우저가 형식을 모르면 확장자로 본다", () => {
  assert.equal(uploadProblem("final_video", { name: "CLIP.MP4", type: "", size: 10 }), null);
  assert.equal(uploadProblem("final_video", { name: "clip.webm", type: "", size: 10 }), "MP4 영상만 올릴 수 있어요");
});

test("크기는 서버 상한(그림 20MB·영상 200MB)까지, 빈 파일은 안 된다", () => {
  const v = UPLOAD_MAX_BYTES.video;
  assert.equal(uploadProblem("final_video", { name: "a.mp4", type: "video/mp4", size: v }), null);
  assert.equal(uploadProblem("final_video", { name: "a.mp4", type: "video/mp4", size: v + 1 }), "파일이 너무 커요 (최대 200MB)");
  assert.equal(uploadProblem("board_image", { name: "a.png", type: "image/png", size: UPLOAD_MAX_BYTES.image + 1 }), "파일이 너무 커요 (최대 20MB)");
  assert.equal(uploadProblem("video", { name: "a.mp4", type: "video/mp4", size: 0 }), "빈 파일이에요");
});

test("끌어 놓기는 파일 하나만 받는다", () => {
  const one = { name: "a.mp4", type: "video/mp4", size: 1 };
  assert.deepEqual(pickDropped([one]), { file: one, problem: null });
  assert.equal(pickDropped([]).problem, "파일이 아니에요");
  assert.equal(pickDropped([one, one]).problem, "한 번에 파일 하나만 올릴 수 있어요");
});

// 11/23 대본: 같은 입력을 마케팅 creative_render.storyboard_video_prompt에 넣어 뽑은 값과 같아야 한다
test("대본을 따른 스토리보드면 장면 흐름·대사·자막·CTA를 붙인다(마케팅과 같은 문장)", () => {
  const shots = [
    { no: 1, start_s: 0, end_s: 2.5, scene_ko: "들판  위 소녀", speaker_ko: "소녀", line_ko: "안녕", caption_ko: "가을" },
    { no: 2, start_s: 2.5, end_s: 5, scene_ko: "노을", line_ko: "함께 봐요" },
    { no: 3, start_s: null, scene_ko: "x" },
  ];
  assert.equal(
    storyboardVideoPrompt("flat 2D", shots, "지금 만나요"),
    `${BASE} flat 2D Follow this script panel by panel: Scene 1 (0-2.5s): 들판 위 소녀 / 소녀: "안녕" / on-screen text "가을". `
      + 'Scene 2 (2.5-5s): 노을 / narration: "함께 봐요". End with the call to action "지금 만나요".',
  );
  assert.equal(storyboardVideoPrompt("flat 2D", [], "지금 만나요"), `${BASE} flat 2D`);
});

test("대본이 길어도 영상 프롬프트는 2000자 안이고 CTA를 남긴다(마케팅과 같은 결과)", () => {
  const long = Array.from({ length: 12 }, (_, k) => {
    const i = k + 1;
    return { no: i, start_s: i * 5, end_s: i * 5 + 5, scene_ko: "긴 장면 ".repeat(30), line_ko: "긴 대사 ".repeat(40), caption_ko: "자막".repeat(20) };
  });
  const p = storyboardVideoPrompt("soft anime ".repeat(15), long, "지금 만나 보세요");
  assert.ok(p.length <= VIDEO_PROMPT_MAX);
  assert.ok(p.endsWith('End with the call to action "지금 만나 보세요".'));
  assert.equal(p.length, 1991);
  assert.equal(createHash("sha256").update(p).digest("hex").slice(0, 16), "a5d2f300cc6355eb");
});

test("초 표기는 소수 한 자리 반올림, 정수면 소수점 없이", () => {
  assert.equal(secs(5), "5");
  assert.equal(secs(2.25), "2.3");
  assert.equal(secs(0.25), "0.3");
  assert.equal(secs(null), "0");
});
