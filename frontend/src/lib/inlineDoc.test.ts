// node --test로 돈다 (npm test). 대화방 의뢰 카드 안 보고서·기획서 펼쳐 보기 (11/26).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { PLAN_MISSING, REPORT_LOADING, expandLabel, inlinePlan, inlineScene, loadFailedKo, missingSvgKo } from "./inlineDoc.ts";
import type { VideoPlanView, VideoSceneView } from "./videoPlan.ts";

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 16"><rect width="9" height="16" fill="#f3efe7"/><text>책</text></svg>';
const scene = (n: number, extra: Partial<VideoSceneView> = {}): VideoSceneView => ({
  n, startS: (n - 1) * 5, endS: n * 5, visual: `장면 ${n} 화면`, caption: "", prompt: "p", ...extra,
});
const plan = (scenes: VideoSceneView[], extra: Partial<VideoPlanView> = {}): VideoPlanView => ({
  concept: "직장인 업무 밈", durationS: 15, aspect: "9:16", script: "대본", scenes, style: "", negative: "", writtenBy: "llm", ...extra,
});

test("펼치기·접기 버튼 글자", () => {
  assert.equal(expandLabel(false), "▸ 펼쳐 보기");
  assert.equal(expandLabel(true), "▾ 접기");
});

test("로딩·실패 문구 (실패하면 [열기]로 다시 볼 수 있다고)", () => {
  assert.equal(REPORT_LOADING, "보고서를 불러오는 중…");
  assert.equal(loadFailedKo("보고서", "서버가 답하지 못했습니다 (502)."), "보고서를 불러오지 못했어요 (서버가 답하지 못했습니다 (502).). [보고서 열기]로 다시 볼 수 있어요.");
  assert.equal(loadFailedKo("기획서"), "기획서를 불러오지 못했어요. [기획서 열기]로 다시 볼 수 있어요.");
  assert.ok(PLAN_MISSING.includes("[기획서 열기]"));
});

test("장면 카드: 3D 콘티는 data URI(svg+xml;base64) · 첫 화면→끝 화면 · 동작 · 카메라", () => {
  const s = inlineScene(scene(1, { startSvg: SVG, endSvg: SVG, startFrame: "책상에 앉은 직원", endFrame: "AI가 서류를 나눠 줌", actionEn: "hands papers", cameraEn: "medium, slow push in", caption: "일 나눠요" }));
  assert.equal(s.title, "장면 1 · 0~5초");
  assert.ok(s.thumb?.startsWith("data:image/svg+xml;base64,"));
  assert.ok(s.thumbEnd?.startsWith("data:image/svg+xml;base64,"));
  assert.ok(!s.thumb?.includes("<svg")); // 글 그대로 넣지 않는다
  const decoded = new TextDecoder().decode(Uint8Array.from(atob(s.thumb!.split(",")[1]), (c) => c.charCodeAt(0)));
  assert.equal(decoded, SVG); // 한글도 깨지지 않는다
  assert.equal(s.frames, "책상에 앉은 직원 → AI가 서류를 나눠 줌");
  assert.equal(s.action, "hands papers");
  assert.equal(s.camera, "medium, slow push in");
  assert.equal(s.caption, "일 나눠요");
  const bare = inlineScene(scene(2, { startS: 2.5, endS: 7 }));
  assert.equal(bare.title, "장면 2 · 2.5~7초");
  assert.deepEqual([bare.thumb, bare.thumbEnd, bare.frames, bare.action, bare.camera, bare.caption], [null, null, null, null, null, null]);
});

test("기획서 요약: 콘셉트·길이 · 규칙 초안 이유 · 콘티 없는 장면 줄", () => {
  const v = inlinePlan(plan([scene(1, { startSvg: SVG }), scene(2), scene(3)]));
  assert.equal(v.concept, "직장인 업무 밈");
  assert.equal(v.meta, "15초 · 9:16 · 장면 3");
  assert.equal(v.fallback, null);
  assert.equal(v.missingSvg, 2);
  assert.equal(missingSvgKo(2, 3), "3D 콘티가 없는 장면이 2개 있어요");
  assert.equal(missingSvgKo(3, 3), "3D 콘티를 그리지 못해 장면 글만 보여요");
  assert.equal(missingSvgKo(0, 3), null);
  const rule = inlinePlan(plan([scene(1)], { writtenBy: "rule", fallbackReason: "timeout" }));
  assert.equal(rule.fallback, "AI가 제한 시간 안에 답하지 못해 규칙으로 썼어요");
});

test("카드 안 그림은 <img src={data URI}>로만 그린다 (SVG 글을 화면에 직접 넣지 않는다)", () => {
  const src = readFileSync(new URL("../components/office/InlineDoc.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(src, /dangerouslySetInnerHTML|innerHTML|<svg\b|<object\b|<iframe\b|<embed\b/);
  const imgs = [...src.matchAll(/<img\b[^>]*\bsrc=\{([^}]+)\}/g)].map((m) => m[1].trim());
  assert.deepEqual(imgs, ["s.thumb", "s.thumbEnd"]); // inlineScene이 만든 data URI만
  // 처음엔 접힘 · 한 번 연 뒤에는 접어도 지우지 않는다(hidden)
  assert.match(src, /useState\(false\)/);
  assert.match(src, /hidden=\{!open\}/);
});

test("카드 버튼 줄: 펼쳐 보기는 [보고서 열기]·[기획서 열기]·[PDF로 저장] 옆에 둔다", () => {
  const card = readFileSync(new URL("../components/office/RequestProgressCard.tsx", import.meta.url), "utf8");
  const chat = readFileSync(new URL("../components/office/ChatCards.tsx", import.meta.url), "utf8");
  assert.match(card, /\{expand\.button\}\s*<button[^\n]*>기획서 열기<\/button>/);
  assert.match(chat, /\{expand\.button\}\s*<button[^\n]*>보고서 열기<\/button>/);
  assert.match(card, /expand\.opened && <InlinePlan/);
  assert.match(chat, /expand\.opened && <InlineReport/);
});
