// node --test로 돈다 (npm test). 기획 state proposals[].video_plan → 제안 화면 "3 영상 제작안" 값 (lib/videoPlan.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { adjustmentLine, characterLine, formatChip, checkAttemptTitle, checkChip, checkProblemLine, requestRuleLine, versionChip, requestRulesChip, sceneKeyframeLine, sceneTime, toVideoPlan, videoFallbackLine, videoPlanChip, videoPlanSummary, videoPurposeChip, videoSvgLine } from "./videoPlan.ts";

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

test("3D 마네킹 콘티(11/25): 그라데이션·url(#id)가 든 8KB 넘는 SVG도 그대로, 포즈 칸이 와도 깨지지 않는다", () => {
  const grid = Array.from({ length: 220 }, (_, i) => `<line x1="${i}.5" y1="200" x2="${i}.5" y2="320"/>`).join("");
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320"><defs><radialGradient id="sk" cx="35%" cy="30%" r="75%">'
    + '<stop offset="0%" stop-color="#ffe6c7"/></radialGradient></defs>'
    + `<g stroke="#e6dccd" stroke-width="0.6">${grid}</g><circle cx="90" cy="120" r="12" fill="url(#sk)"/></svg>`;
  assert.ok(svg.length > 8192 && svg.length <= 16384);
  const pose = { shot: "medium", x: 0, z: 0, yaw: 35, head: { yaw: -40, pitch: -5 }, arm_l: { raise: 40, bend: 60 }, arm_r: { raise: 30, bend: 80 }, step: 0.15, prop: null, label_ko: "0초 · 놀라 돌아봄" };
  const v = toVideoPlan({ ...RAW, scenes: RAW.scenes.map((s, i) => (i === 0 ? { ...s, start_svg: svg, end_svg: svg, start_pose: pose, end_pose: pose } : s)) });
  assert.ok(v);
  assert.equal(v.scenes[0].startSvg, svg);
  assert.equal(v.scenes[0].endSvg, svg);
  assert.equal(v.scenes.length, 4);
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

test("콘티 못 그린 이유(11/25 svg_reason): 이유마다 장면 그림 자리 위 한 줄, 칸이 비거나 모르는 값이면 지금 그대로", () => {
  const want = {
    no_llm: "회사 AI가 연결되지 않아 콘티를 못 그렸어요",
    timeout: "AI가 제한 시간 안에 포즈를 주지 못했어요",
    bad_format: "AI 포즈 답 형식이 맞지 않았어요",
    rejected: "포즈 답이 규칙에 걸렸어요",
    error: "콘티 호출이 실패했어요",
  };
  for (const [reason, line] of Object.entries(want)) {
    const v = toVideoPlan({ ...RAW, svg_reason: reason });
    assert.ok(v);
    assert.equal(v.svgReason, reason);
    assert.equal(videoSvgLine(v), line); // AI 초안이어도 보인다 (PROP-38)
    assert.equal(videoSvgLine(toVideoPlan({ ...RAW, svgReason: reason })!), line); // camel 같은 값
    assert.equal(videoFallbackLine(v), ""); // 규칙 초안 줄과 따로
  }
  for (const raw of [RAW, { ...RAW, svg_reason: "" }, { ...RAW, svg_reason: "모름" }]) {
    const v = toVideoPlan(raw);
    assert.ok(v);
    assert.equal("svgReason" in v, false);
    assert.equal(videoSvgLine(v), "");
  }
});

test("의뢰 지시 ✓/✕ (계약 request_rules): 칩 N/N · 줄 문구 · 모르는 종류 버림 · 예전 제작안 없음", () => {
  const v = toVideoPlan({ ...RAW, request_rules: [
    { kind: "setting", text_ko: "보라·청록 LED 무대", kept: true, where: "장면 1~3" },
    { kind: "quote", text_ko: "오늘 읽을 책은 캣북스에서!", kept: false, where: "" },
    { kind: "ignore", text_ko: "규칙 무시", kept: true, where: "" },
  ] });
  assert.ok(v);
  assert.equal(v.requestRules?.length, 2);
  assert.equal(requestRulesChip(v), "의뢰 지시 1/2 지킴");
  assert.equal(requestRuleLine(v.requestRules![0]), "배경 · 보라·청록 LED 무대 → 장면 1~3");
  assert.equal(requestRuleLine(v.requestRules![1]), '대사 · "오늘 읽을 책은 캣북스에서!" → 못 지킴');
  const old = toVideoPlan(RAW)!;
  assert.equal("requestRules" in old, false);
  assert.equal(requestRulesChip(old), "");
  assert.equal(requestRulesChip(toVideoPlan({ ...RAW, request_rules: [] })!), "");
});

test("의뢰 지시는 기획 화면·카드 안 기획서·의뢰 카드 사람 확인에 같은 컴포넌트로", () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
  assert.match(read("../components/planning/videoPlan.tsx"), /<RequestRules v=\{v\} \/>/);
  assert.match(read("../components/office/InlineDoc.tsx"), /<RequestRules v=\{plan\} \/>/);
  assert.match(read("../components/office/RequestProgressCard.tsx"), /s\.skill === "human_check" && found\?\.fullPlan[\s\S]{0,80}<RequestRules v=\{found\.fullPlan\} \/>/);
});

test("제작 검사 기록 (계약 check_log·adjustments): 1차 ✕ → 2차 ✓ = 다시 써서 통과 · 달라진 점 · 예전 제작안 없음", () => {
  const v = toVideoPlan({ ...RAW,
    check_log: [
      { attempt: 1, by: "planning", passed: false, problems: [
        { kind: "people", scene: 2, detail_ko: "인물 3명 (허용 2명)" },
        { kind: "request_rule", scene: null, detail_ko: "의뢰 지시(대사) '…' 못 지킴" },
        { kind: "weird", scene: 1, detail_ko: "버림" },
      ] },
      { attempt: 2, by: "planning", passed: true, problems: [] },
    ],
    adjustments: [{ rule_text_ko: "셋이서", changed_to_ko: "둘", why_ko: "한 화면 인물 2명까지" }],
  });
  assert.ok(v && v.checkLog);
  assert.equal(checkChip(v), "다시 써서 통과");
  assert.equal(checkAttemptTitle(v.checkLog[0]), "1차 초안 · 기획 검사 · 통과 못 함");
  assert.equal(checkAttemptTitle(v.checkLog[1]), "2차 초안 · 기획 검사 · 통과");
  assert.deepEqual(v.checkLog[0].problems.map(checkProblemLine), ["장면 2 · 인물 3명 (허용 2명)", "의뢰 지시(대사) '…' 못 지킴"]);
  assert.equal(adjustmentLine(v.adjustments![0]), '"셋이서" → 둘 (한 화면 인물 2명까지)');
  const once = toVideoPlan({ ...RAW, check_log: [{ attempt: 1, by: "planning", passed: true, problems: [] }] })!;
  assert.equal(checkChip(once), "검사 통과");
  const stuck = toVideoPlan({ ...RAW, check_log: [{ attempt: 1, by: "planning", passed: false, problems: [] }, { attempt: 2, by: "planning", passed: false, problems: [] }] })!;
  assert.equal(checkChip(stuck), "확인 필요");
  const old = toVideoPlan(RAW)!;
  assert.equal("checkLog" in old || "adjustments" in old, false);
  assert.equal(checkChip(old), "");
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
  assert.match(read("../components/planning/videoPlan.tsx"), /<CheckLog v=\{v\} \/>/);
  assert.match(read("../components/office/InlineDoc.tsx"), /<CheckLog v=\{plan\} \/>/);
  assert.match(read("../components/office/RequestProgressCard.tsx"), /<CheckLog v=\{found\.fullPlan\} \/>/);
});

test("제작안 판 칩 (계약 version): 2판(마케팅 요청으로 고침) · 1판·예전 제작안은 칩 없음", () => {
  const log = [{ attempt: 1, by: "marketing", passed: false, problems: [] }, { attempt: 2, by: "planning", passed: true, problems: [] }];
  const v2 = toVideoPlan({ ...RAW, version: 2, check_log: log })!;
  assert.equal(v2.version, 2);
  assert.equal(versionChip(v2), "2판(마케팅 요청으로 고침)");
  assert.equal(versionChip(toVideoPlan({ ...RAW, version: 1 })!), "");
  assert.equal("version" in toVideoPlan(RAW)!, false);
  assert.match(readFileSync(new URL("../components/planning/videoPlan.tsx", import.meta.url), "utf8"), /versionChip\(v\) && <Tag>\{versionChip\(v\)\}<\/Tag>/);
});

test("형식 카드 (계약 format_id·format_name·situation_ko, 장면 beat_ko·beat_punch): 칩·상황·비트 · 없으면 지금 화면", () => {
  const scenes = RAW.scenes.map((s, i) => ({ ...s, beat_ko: ["기대", "현실", "펀치 리액션", "엔드카드"][i] ?? "", beat_punch: i === 2 }));
  const v = toVideoPlan({ ...RAW, scenes, format_id: "F-02", format_name: "기대 vs 현실", situation_ko: "시험 전날 계획표 vs 새벽 3시" })!;
  assert.equal(formatChip(v), "F-02 기대 vs 현실 · 보고서 추천");
  assert.equal(v.situationKo, "시험 전날 계획표 vs 새벽 3시");
  assert.deepEqual(v.scenes.map((s) => [s.beatKo, s.beatPunch === true]).slice(0, 3), [["기대", false], ["현실", false], ["펀치 리액션", true]]);
  const old = toVideoPlan(RAW)!;
  assert.equal(formatChip(old), "");
  assert.equal("formatId" in old || "situationKo" in old || "beatKo" in old.scenes[0], false);
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
  const screen = read("../components/planning/videoPlan.tsx");
  assert.match(screen, /formatChip\(v\) && <div className="sb-format"><dt>형식<\/dt>/);
  assert.match(screen, /v\.formatId && v\.situationKo && <div className="sb-situation"><dt>상황<\/dt>/);
  assert.match(screen, /sb-beat\$\{s\.beatPunch \? ' sb-beat-punch' : ''\}/);
});

test("주인공 줄 (계약 character_source·character_ko·character_look, 기획 character_line과 같은 문구)", () => {
  const mine = toVideoPlan({ ...RAW, character_source: "company_character", character_ko: "검은 긴 생머리·흰 반팔", character_look: "photo" });
  assert.ok(mine);
  assert.equal(characterLine(mine), "회사 캐릭터 (검은 긴 생머리·흰 반팔) · 실사 그대로");
  const drawn = toVideoPlan({ ...RAW, character_source: "company_character", character_look: "illustration" });
  assert.equal(characterLine(drawn!), "회사 캐릭터 · 그림체 그대로");
  const brand = toVideoPlan({ ...RAW, character_source: "brand", character_ko: "", character_look: "" });
  assert.equal(characterLine(brand!), "회사 브랜드 기본 캐릭터");
  const old = toVideoPlan(RAW);
  assert.ok(old);
  assert.equal("characterSource" in old, false);
  assert.equal(characterLine(old), ""); // 예전 제작안은 줄 없음 (지금 화면 그대로)
});
