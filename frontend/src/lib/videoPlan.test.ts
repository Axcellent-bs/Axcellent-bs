// node --test로 돈다 (npm test). 기획 state proposals[].video_plan → 제안 화면 "3 영상 제작안" 값 (lib/videoPlan.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { sceneTime, toVideoPlan, videoPlanChip, videoPlanSummary } from "./videoPlan.ts";

// 11/20 16:55 서버 PROP-15 모양 (snake_case, 계약 VideoPlan)
const RAW = {
  concept: "여성 10대에게 책 고르는 재미와 첫 문장에 빠지는 순간을 15초 숏폼으로",
  duration_s: 15, aspect: "9:16",
  script: "장면 1(훅): 책상 위 필기구 클로즈업…",
  scenes: [
    { n: 1, start_s: 0, end_s: 2, visual: "나무 책상 클로즈업", caption: "이번엔 어떤 소설 읽지?", prompt: "close-up of a wooden desk" },
    { n: 2, start_s: 2, end_s: 6, visual: "펼친 소설 페이지", caption: "", prompt: "open novel page" },
    { n: 3, start_s: 6, end_s: 11, visual: "문장 아래 밑줄", caption: "마음에 남은 문장", prompt: "underline a sentence" },
    { n: 4, start_s: 11, end_s: 15, visual: "다음 책을 기대하는 마무리", caption: "다음 책은?", prompt: "closing shot" },
  ],
  style: "warm cream, soft light", negative: "no text, no logos", written_by: "llm",
  media_hints: { season: "winter", color_theme: "warm cream", category: null },
};

test("toVideoPlan: 정상 (snake_case 원본 · camelize 뒤 둘 다 같은 값)", () => {
  const v = toVideoPlan(RAW);
  assert.ok(v);
  assert.equal(v.durationS, 15);
  assert.equal(v.aspect, "9:16");
  assert.equal(v.writtenBy, "llm");
  assert.equal(v.scenes.length, 4);
  assert.deepEqual(v.scenes[1], { n: 2, startS: 2, endS: 6, visual: "펼친 소설 페이지", caption: "", prompt: "open novel page" });
  assert.deepEqual(v.mediaHints, { season: "winter", colorTheme: "warm cream", category: undefined });
  const camel = {
    concept: RAW.concept, durationS: 15, aspect: "9:16", script: RAW.script, style: RAW.style, negative: RAW.negative, writtenBy: "llm",
    scenes: RAW.scenes.map((s) => ({ n: s.n, startS: s.start_s, endS: s.end_s, visual: s.visual, caption: s.caption, prompt: s.prompt })),
    mediaHints: { season: "winter", colorTheme: "warm cream", category: null },
  };
  assert.deepEqual(toVideoPlan(camel), v);
  assert.equal(sceneTime(v.scenes[0]), "0~2초");
  assert.equal(videoPlanChip(v), "AI 초안 · 15초 · 9:16");
  assert.equal(videoPlanSummary(v), "영상 제작안 · 15초 · 9:16 · 장면 4");
});

test("toVideoPlan: 선택 칸이 빠지면 기본값 (aspect 9:16 · written_by rule · 자막·화풍·금지 빈 글 · 힌트 없음)", () => {
  const { aspect: _a, written_by: _w, media_hints: _m, style: _s, negative: _n, ...rest } = RAW;
  const v = toVideoPlan({ ...rest, scenes: RAW.scenes.map(({ caption: _c, ...s }) => s) });
  assert.ok(v);
  assert.equal(v.aspect, "9:16");
  assert.equal(v.writtenBy, "rule");
  assert.equal(v.style, "");
  assert.equal(v.negative, "");
  assert.equal(v.scenes[0].caption, "");
  assert.equal(v.mediaHints, undefined);
  assert.equal(videoPlanChip({ ...v, writtenBy: "human" }), "사람 · 15초 · 9:16");
});

test("toVideoPlan: 없거나 모양이 틀리면 undefined (던지지 않는다)", () => {
  for (const bad of [
    undefined, null, "plan", 3, [], {},
    { ...RAW, concept: "" },
    { ...RAW, duration_s: "15" },
    { ...RAW, scenes: [] },
    { ...RAW, scenes: "장면" },
    { ...RAW, scenes: [...RAW.scenes, { n: 5, start_s: 15, end_s: 15, visual: "x", prompt: "y" }] }, // 끝이 시작보다 크지 않음
    { ...RAW, scenes: [{ n: 1, start_s: 0, end_s: 2, prompt: "y" }] }, // 화면 구성 없음
    { ...RAW, scenes: [null] },
    { ...RAW, aspect: "4:3" },
  ]) {
    assert.equal(toVideoPlan(bad), undefined, JSON.stringify(bad)?.slice(0, 60));
  }
});
