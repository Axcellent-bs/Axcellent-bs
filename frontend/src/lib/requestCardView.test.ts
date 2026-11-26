// node --test (npm test). 대화방 의뢰 진행 카드 크게·한눈에 (11/26 시안 ⓪ 접힘 · ① 진행 중 · ② 끝남).
// 계산은 lib/requestCardView.ts, 화면 구조는 RequestProgressCard.tsx 소스의 클래스·구조로 본다(폭 400px 스냅샷 대신).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { stepRows } from "./requestCards.ts";
import {
  TONE_CLASS, barCells, creativeSpot, currentIndex, deptTag, futureLine, headPill, nowLine, peekText, shortLabel, stepState,
} from "./requestCardView.ts";
import type { RequestStatus, StepStatus, WorkRequestPlanStep, WorkRequestStep } from "./workRequests.ts";

// 홍보 영상 계획 7단계 (총괄 request_tasks.PROMO_VIDEO 순서)
const PLAN: WorkRequestPlanStep[] = [
  { step_id: "character", dept: null, skill: "brand_character", label: "회사 캐릭터 고르기", after: [], kind: "human" },
  { step_id: "trend", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: ["character"] },
  { step_id: "insight", dept: "data", skill: "trend_report_insight", label: "기획에 넘길 인사이트", after: ["trend"] },
  { step_id: "plan", dept: "planning", skill: "receive_insight", label: "영상 제작안", after: ["insight"] },
  { step_id: "confirm", dept: null, skill: "human_check", label: "사람 확인", after: ["plan"], kind: "human" },
  { step_id: "prepare", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 초안 준비", after: ["confirm"] },
  { step_id: "make", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["prepare"] },
];
const step = (status: StepStatus, reason_code: string | null = null): WorkRequestStep => ({
  status, attempts: 0, grade: null, note: "", reason_code, result_ref: null, review_id: null, handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null,
});
/** 앞 n단계는 완료, 그다음 단계는 cur 상태, 나머지는 대기 */
function rowsAt(n: number, cur: StepStatus, reason: string | null = null) {
  const steps: Record<string, WorkRequestStep> = {};
  PLAN.forEach((p, i) => { steps[p.step_id] = i < n ? step("completed") : i === n ? step(cur, reason) : step("pending"); });
  return { rows: stepRows({ plan: PLAN, steps }), steps };
}
const withReason = (r: ReturnType<typeof rowsAt>) => r.rows.map((s) => ({ ...s, reasonCode: r.steps[s.stepId]?.reason_code ?? null }));

test("사람 차례(사람 확인 대기): 주황 알약 · 막대 7칸(끝 4 · 지금 주황 1 · 남은 2) · 지금 5단계 / 7", () => {
  const r = rowsAt(4, "waiting_human", "human_check");
  assert.deepEqual(headPill(r.rows, "waiting"), { label: "● 사람 확인 기다려요", tone: "wait" });
  assert.deepEqual(barCells(r.rows, "waiting"), ["ok", "ok", "ok", "ok", "wait", "todo", "todo"]);
  const now = nowLine(withReason(r), "waiting", true);
  assert.equal(now.text, "지금 5단계 / 7 · 사람 확인 · 진행할지 정해 주세요");
  assert.equal(now.short, "지금 5/7 · 사람 확인 · 진행할지 정해 주세요");
  assert.equal(now.tone, "wait");
  assert.deepEqual(stepState(r.rows[4], true), { text: "지금 할 일", tone: "wait" });
});

test("진행 중: 브랜드 알약 · 지금 칸 브랜드 · 부서가 하는 중", () => {
  const r = rowsAt(1, "running");
  assert.deepEqual(headPill(r.rows, "running"), { label: "진행 중", tone: "run" });
  assert.deepEqual(barCells(r.rows, "running"), ["ok", "run", "todo", "todo", "todo", "todo", "todo"]);
  const now = nowLine(withReason(r), "running", true);
  assert.equal(now.text, "지금 2단계 / 7 · 데이터분석 · 트렌드 분석 보고서 하는 중");
  assert.equal(now.tone, "run");
  assert.deepEqual(stepState(r.rows[1], true), { text: "진행 중", tone: "run" });
  assert.deepEqual(stepState(r.rows[2], false), { text: "대기", tone: "todo" });
});

test("끝남: 초록 알약 · 7칸 모두 초록 · ✓ 영상 완성 (영상 의뢰가 아니면 ✓ 모두 끝났어요)", () => {
  const r = rowsAt(7, "completed");
  assert.deepEqual(headPill(r.rows, "done"), { label: "✓ 끝남", tone: "ok" });
  assert.deepEqual(barCells(r.rows, "done"), Array(7).fill("ok"));
  assert.equal(currentIndex(r.rows, "done"), null);
  assert.equal(nowLine(withReason(r), "done", true).text, "✓ 영상 완성");
  assert.equal(nowLine(withReason(r), "done", false).text, "✓ 모두 끝났어요");
  assert.equal(stepState(r.rows[4], false).text, "진행함"); // 사람 확인을 진행했다
  assert.equal(stepState(r.rows[3], false).text, "완료");
});

test("실패: 빨강 알약 · 실패 칸 빨강 · 그 단계에서 멈췄어요", () => {
  const r = rowsAt(5, "failed");
  for (const s of ["failed", "rejected"] as RequestStatus[]) assert.deepEqual(headPill(r.rows, s), { label: "멈춤·실패", tone: "bad" });
  assert.deepEqual(barCells(r.rows, "failed"), ["ok", "ok", "ok", "ok", "ok", "bad", "todo"]);
  assert.equal(nowLine(withReason(r), "failed", true).text, "지금 6단계 / 7 · 영상 초안 준비에서 멈췄어요");
  assert.deepEqual(stepState(r.rows[5], true), { text: "실패", tone: "bad" });
  assert.deepEqual(stepState({ status: "skipped", skill: "x" }, false), { text: "건너뜀", tone: "todo" });
});

test("사람 차례 할 일 문장: 캐릭터 답 · 영상 승인 · 재시작 멈춤", () => {
  assert.equal(nowLine(withReason(rowsAt(0, "waiting_human", "character_wait")), "waiting", true).todo, "회사 캐릭터를 정해 주세요");
  assert.equal(nowLine(withReason(rowsAt(6, "held", "video_approval")), "waiting", true).todo, "영상 승인을 기다려요 · 승인 화면에서 승인해 주세요");
  assert.equal(nowLine(withReason(rowsAt(2, "waiting_human", "restart_unconfirmed")), "waiting", true).todo, "멈춘 단계를 다시 보낼지 정해 주세요");
});

test("막대 아래 짧은 이름 · 부서 꼬리표(사람·데이터분석·기획·마케팅, 부서마다 다른 글자색) · 남은 단계 한 줄", () => {
  const r = rowsAt(0, "pending");
  assert.deepEqual(r.rows.map(shortLabel), ["캐릭터", "보고서", "인사이트", "제작안", "사람 확인", "준비", "영상"]);
  const tags = r.rows.map((s) => deptTag(s.dept));
  assert.deepEqual(tags.map((t) => t.label), ["사람", "데이터분석", "데이터분석", "기획", "사람", "마케팅", "마케팅"]);
  assert.equal(new Set([deptTag("data").cls, deptTag("planning").cls, deptTag("marketing").cls, deptTag(null).cls]).size, 4);
  assert.equal(futureLine("prepare_video_draft_from_plan"), "시트 · 대본 · 스토리보드 · 제작안 확인(필요하면 기획에 고치기 요청) · 스토리보드 그림 1장(약 1.5 크레딧)");
  assert.equal(futureLine("generate_media"), "영상 승인(1시간) 뒤 만들어요");
  assert.equal(futureLine("unknown_skill"), null);
});

test("색은 AXcellent 의미 토큰만 (hex·rgb 직접 값·토큰 밖 색 이름 없음)", () => {
  const all = Object.values(TONE_CLASS).flatMap((t) => Object.values(t)).concat(["data", "planning", "marketing", null].map((d) => deptTag(d).cls)).join(" ");
  assert.doesNotMatch(all, /#|rgb\(|\b(?:sky|orange|blue|cyan|purple|white|black)-/);
  for (const cls of all.split(/\s+/).filter(Boolean)) assert.match(cls, /^(?:bg|text)-(?:ink|brand|amber|emerald|red|violet|surface)(?:-\d+)?$/, cls);
});

test("접힌 카드 설명: 캐릭터 줄 → 제작안 콘셉트 → 의뢰 원문", () => {
  assert.equal(peekText("회사 캐릭터: 긴 생머리(CR-a)", "콘셉트", "원문"), "회사 캐릭터: 긴 생머리(CR-a)");
  assert.equal(peekText(null, "무대 리액션 영상", "원문"), "무대 리액션 영상");
  assert.equal(peekText(undefined, " ", "원문"), "원문");
  assert.equal(peekText(null, null, ""), null);
});

test("이미지 시트·승인·기술 검사 칸 자리: 끝나면 머리 영상 카드 · 영상 단계가 시작했으면 그 줄 · 준비 줄 · 아니면 맨 아래", () => {
  assert.equal(creativeSpot(rowsAt(7, "completed").rows, "done", true), "head");
  assert.equal(creativeSpot(rowsAt(6, "held", "video_approval").rows, "waiting", false), "make");
  assert.equal(creativeSpot(rowsAt(5, "running").rows, "running", false), "prepare");
  assert.equal(creativeSpot(rowsAt(3, "running").rows, "running", false), "bottom");
});

// ---- 화면 구조 (소스)

const card = readFileSync(new URL("../components/office/RequestProgressCard.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

test("다른 코드·시험이 찾는 aria-label 그대로 · 접기 버튼 aria-expanded · 막대는 aria-hidden(글자 '지금 N단계 / 7'이 대신)", () => {
  assert.match(card, /aria-label=\{`의뢰 진행 \$\{row\.request_id\}`\}/);
  assert.match(card, /aria-expanded=\{open\}[\s\S]{0,120}onClick=\{toggle\}/);
  assert.match(card, /function Bar[\s\S]*?<div aria-hidden="true"/);
  assert.match(card, /function MiniBar[\s\S]*?aria-hidden="true"/);
  assert.match(card, /\{now\.text\}/); // 막대 위 글자
  assert.match(card, /펼쳐서 단계 보기 ▾<\/button>/);
  assert.match(card, /useState\(true\)/); // 펼침이 기본 (지금 그대로)
});

test("글씨: 본문 15px · 단계 이름 16px 굵게 · 제목 19px · 카드 안 11px 이하 글씨 없음 · 큰 버튼은 15px", () => {
  assert.match(card, /req-card[^"`]*text-\[15px\]/);
  assert.match(card, /<b className="text-base font-bold">\{s\.label\}<\/b>/);
  assert.match(card, /<h3 className="[^"]*text-\[19px\] font-bold/);
  assert.doesNotMatch(card, /text-\[(?:9|10|10\.5|11|11\.5)px\]|\btext-xs\b/);
  assert.match(css, /\.req-card \.req-now \.btn-card \{[^}]*font-size: 15px/);
  assert.match(css, /\.req-card \.text-xs \{ font-size: 13\.5px/);
  assert.match(css, /\.req-card :is\(button, a, summary\):focus-visible/);
});

test("지금 단계 버튼은 기존 decide를 부른다(큰 버튼 줄 .req-now) · 실패 문구도 그 줄 안", () => {
  assert.match(card, /<span className="req-now[^"]*">\s*<button type="button" className="btn-primary btn-card" disabled=\{deciding !== null\} onClick=\{\(\) => decide\(s\.stepId, true\)\}>\{s\.decide\.approve\}/);
  assert.match(card, /onClick=\{\(\) => decide\(s\.stepId, false\)\}>\{s\.decide\.stop\}/);
  assert.match(card, /failed\[s\.stepId\] && <p role="alert"/);
});

test("끝남이면 머리 영상 카드: [영상 열기](큰 버튼) + 이미지 시트·스토리보드 칸 · 영상 열기는 한 곳에만", () => {
  assert.match(card, /spot === "head" && \([\s\S]{0,120}aria-label="만든 영상"[\s\S]{0,400}<span className="req-now[^"]*">\{videoButton\}<\/span>\s*\{creative\}/);
  assert.equal(card.match(/>영상 열기<\/button>/g)?.length, 1);
  assert.equal(card.match(/<RequestCreative /g)?.length, 1);
});

test("끝난 제작안 줄: 주인공 · 의뢰 지시 · 제작 검사 (사람 확인 중이면 그 단계에만)", () => {
  assert.match(card, /isInsight && found\?\.fullPlan && !humanDeciding && \([\s\S]{0,200}주인공: \{lead\}[\s\S]{0,120}<RequestRules v=\{found\.fullPlan\} \/>\s*<CheckLog v=\{found\.fullPlan\} \/>/);
});
