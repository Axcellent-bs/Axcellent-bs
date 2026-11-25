// node --test로 돈다 (npm test). 기획 state proposals[].video_plan → 제안 화면 "3 영상 제작안" 값 (lib/videoPlan.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { sceneKeyframeLine, sceneTime, toVideoPlan, videoFallbackLine, videoPlanChip, videoPlanSummary, videoPurposeChip } from "./videoPlan.ts";

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

// 11/25 키프레임 칸·광고 목적 (계약 VideoScene.start_frame…camera_en, VideoPlan.purpose·offer_ko·cta_ko)
const KEYFRAME = {
  ...RAW,
  purpose: "client_offer", offer_ko: "겨울 신간 할인", cta_ko: "지금 만나 보세요",
  scenes: RAW.scenes.map((s, i) => (i === 0
    ? { ...s, start_frame: "고양이 한 마리가 가운데에서 창문을 본다", end_frame: "고양이가 카메라를 본다", action_en: "The cat turns toward the camera", camera_en: "Medium shot, slow push-in" }
    : s)),
};

test("키프레임 한 줄: 첫 화면 → 끝 화면 · 동작 · 카메라 (snake·camel 같은 값)", () => {
  const v = toVideoPlan(KEYFRAME);
  assert.ok(v);
  assert.equal(sceneKeyframeLine(v.scenes[0]), "고양이 한 마리가 가운데에서 창문을 본다 → 고양이가 카메라를 본다 · The cat turns toward the camera · Medium shot, slow push-in");
  assert.equal(sceneKeyframeLine(v.scenes[1]), null); // 빈 칸이면 줄 없음 (지금 화면 그대로)
  assert.deepEqual(toVideoPlan(v), v); // camelize 뒤(화면 값 모양)도 같은 값
  assert.equal(sceneKeyframeLine({ startFrame: "", endFrame: "", actionEn: "The cat waves", cameraEn: "" }), "The cat waves");
});

test("키프레임: 규칙 제작안처럼 첫 화면만 있으면 줄을 그리지 않는다", () => {
  assert.equal(sceneKeyframeLine({ startFrame: "나무 책상 클로즈업" }), null);
});

test("광고 목적 칩: 솔루션 광고 · 고객 혜택 광고 · 채널 콘텐츠, 없거나 모르는 값이면 없음", () => {
  const v = toVideoPlan(KEYFRAME);
  assert.ok(v);
  assert.equal(videoPurposeChip(v), "고객 혜택 광고");
  assert.equal(v.offerKo, "겨울 신간 할인");
  assert.equal(v.ctaKo, "지금 만나 보세요");
  assert.equal(videoPurposeChip(toVideoPlan({ ...RAW, purpose: "solution_ad" })!), "솔루션 광고");
  assert.equal(videoPurposeChip(toVideoPlan({ ...RAW, purpose: "channel_content" })!), "채널 콘텐츠");
  const old = toVideoPlan(RAW)!;
  assert.equal(videoPurposeChip(old), null);
  assert.equal(old.offerKo, undefined);
  assert.equal(videoPurposeChip(toVideoPlan({ ...RAW, purpose: "meme" })!), null);
  assert.deepEqual(Object.keys(old.scenes[0]).sort(), ["caption", "endS", "n", "prompt", "startS", "visual"]); // 예전 제작안은 새 칸 없음
});

test("SVG 콘티: start_svg·end_svg를 읽고(snake·camel), 없으면 칸을 두지 않는다", () => {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 160"><rect x="1" y="1" width="5" height="5"/></svg>';
  const v = toVideoPlan({ ...RAW, scenes: RAW.scenes.map((s, i) => (i === 0 ? { ...s, start_svg: svg, end_svg: svg } : s)) });
  assert.ok(v);
  assert.equal(v.scenes[0].startSvg, svg);
  assert.equal(v.scenes[0].endSvg, svg);
  assert.equal("startSvg" in v.scenes[1], false); // 예전 제작안은 칸이 없다 (화면은 지금 색 칸 그대로)
  assert.deepEqual(toVideoPlan(v), v);
});

test("규칙 초안 이유: 이유마다 칩 옆 한 줄, 칸이 비거나 모르는 값이면 지금 그대로(빈 줄)", () => {
  const want = {
    no_llm: "회사 AI가 연결되지 않아 규칙으로 썼어요",
    timeout: "AI가 제한 시간 안에 답하지 못해 규칙으로 썼어요",
    bad_format: "AI 답 형식이 맞지 않아 규칙으로 썼어요",
    rejected: "AI 답이 금지 규칙에 걸려 규칙으로 썼어요",
    error: "AI 호출이 실패해 규칙으로 썼어요",
  };
  for (const [reason, line] of Object.entries(want)) {
    const v = toVideoPlan({ ...RAW, written_by: "rule", fallback_reason: reason });
    assert.ok(v);
    assert.equal(v.fallbackReason, reason);
    assert.equal(videoFallbackLine(v), line);
    assert.equal(videoFallbackLine(toVideoPlan({ ...RAW, writtenBy: "rule", fallbackReason: reason })!), line); // camel 같은 값
    assert.equal(videoPlanChip(v), "규칙 초안 · 15초 · 9:16"); // 칩은 그대로
  }
  for (const raw of [{ ...RAW, written_by: "rule" }, { ...RAW, written_by: "rule", fallback_reason: "" }, { ...RAW, written_by: "rule", fallback_reason: "모름" }]) {
    const v = toVideoPlan(raw);
    assert.ok(v);
    assert.equal("fallbackReason" in v, false);
    assert.equal(videoFallbackLine(v), "");
  }
  // LLM 제작안이면 이유 칸이 와도 줄을 그리지 않는다
  assert.equal(videoFallbackLine(toVideoPlan({ ...RAW, fallback_reason: "timeout" })!), "");
});
