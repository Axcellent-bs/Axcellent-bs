// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { HIGGSFIELD_URL, UPLOAD_MAX_BYTES, acceptFor, pickDropped, storyboardVideoPrompt, uploadProblem } from "./higgsfieldWeb.ts";

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
