// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { HIGGSFIELD_URL, NO_TEXT, UPLOAD_MAX_BYTES, VIDEO_PROMPT_MAX, acceptFor, pickDropped, secs, storyboardVideoPrompt, uploadProblem } from "./higgsfieldWeb.ts";

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
  assert.equal(uploadProblem("motion_video", { name: "dance.mp4", type: "video/mp4", size: 10 }), null);
  assert.equal(uploadProblem("motion_video", { name: "dance.png", type: "image/png", size: 10 }), "MP4 영상만 올릴 수 있어요");
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
// 11/25: 자막·CTA는 넣지 않고(서버가 코드로 얹는다) 대사는 따옴표 없이, 끝에 글자 금지 문장
test("대본을 따른 스토리보드면 장면 흐름·대사 요지 + 글자 금지 문장(마케팅과 같은 문장)", () => {
  const shots = [
    { no: 1, start_s: 0, end_s: 2.5, scene_ko: "들판  위 소녀", speaker_ko: "소녀", line_ko: "안녕", caption_ko: "가을" },
    { no: 2, start_s: 2.5, end_s: 5, scene_ko: "노을", line_ko: "함께 봐요" },
    { no: 3, start_s: null, scene_ko: "x" },
  ];
  assert.equal(
    storyboardVideoPrompt("flat 2D", shots),
    `${BASE} flat 2D Follow this script panel by panel: Scene 1 (0-2.5s): 들판 위 소녀 / the action conveys: 안녕. `
      + `Scene 2 (2.5-5s): 노을 / the action conveys: 함께 봐요. ${NO_TEXT}`,
  );
  assert.equal(storyboardVideoPrompt("flat 2D", []), `${BASE} flat 2D`);
});

test("대본이 길어도 영상 프롬프트는 2000자 안이고 글자 금지 문장을 남긴다(마케팅과 같은 결과)", () => {
  const long = Array.from({ length: 12 }, (_, k) => {
    const i = k + 1;
    return { no: i, start_s: i * 5, end_s: i * 5 + 5, scene_ko: "긴 장면 ".repeat(30), line_ko: "긴 대사 ".repeat(40), caption_ko: "자막".repeat(20) };
  });
  const p = storyboardVideoPrompt("soft anime ".repeat(15), long, { speed: 1.2 });
  assert.ok(p.length <= VIDEO_PROMPT_MAX);
  assert.ok(p.endsWith(NO_TEXT));
  assert.ok(p.includes("Scene 12 (72-78s)"));
  // 마케팅 test_video_prompt_no_text.py와 같은 길이·해시
  assert.equal(p.length, 1991);
  assert.equal(createHash("sha256").update(p).digest("hex").slice(0, 16), "22627dd87292ec68");
});

// 마케팅 test_video_prompt_no_text.py의 FIXED와 글자 하나까지 같다
const PERSON = "Make one continuous short film of the same person as the reference images, keeping face, hair and outfit consistent.";
const FIXED_SHOTS = [
  { no: 1, start_s: 0, end_s: 3, scene_ko: "들판  위 소녀", speaker_ko: "소녀", line_ko: "안녕", caption_ko: "가을" },
  { no: 2, start_s: 3, end_s: 5, scene_ko: "노을", line_ko: "함께 봐요" },
  { no: 3, start_s: null, scene_ko: "x" },
];
const FIXED = `${PERSON} flat 2D Follow this script panel by panel: `
  + "Scene 1 (0-3.6s): 들판 위 소녀 / the action conveys: 안녕. "
  + "Scene 2 (3.6-6s): 노을 / the action conveys: 함께 봐요. "
  + "No on-screen text, captions, subtitles, logos or signs; text is added later.";

test("고정 예시: 가상 인물 참조 + 1.2배속이면 마케팅과 같은 문장", () => {
  assert.equal(storyboardVideoPrompt("flat 2D", FIXED_SHOTS, { speed: 1.2, reference: "character_original" }), FIXED);
});

test("자막·CTA·따옴표 없음, 장면 시각 × 배속, 참조 방식별 첫 문장", () => {
  const p = storyboardVideoPrompt("flat 2D", FIXED_SHOTS);
  assert.ok(!p.includes("가을") && !p.includes("call to action") && !p.includes('"') && !p.includes("narration"));
  assert.ok(p.includes("Scene 1 (0-3s)") && p.endsWith(NO_TEXT));
  assert.ok(storyboardVideoPrompt(null, FIXED_SHOTS, { speed: 1.5 }).includes("Scene 2 (4.5-7.5s)"));
  assert.equal(storyboardVideoPrompt("x", null, { reference: "character_original" }), `${PERSON} x`);
  assert.equal(storyboardVideoPrompt("x", null, { reference: "storyboard_image" }), `${BASE} x`);
  assert.equal(storyboardVideoPrompt(null, null, { reference: "unknown", speed: null }), BASE);
});

test("초 표기는 소수 한 자리 반올림, 정수면 소수점 없이", () => {
  assert.equal(secs(5), "5");
  assert.equal(secs(2.25), "2.3");
  assert.equal(secs(0.25), "0.3");
  assert.equal(secs(null), "0");
});
